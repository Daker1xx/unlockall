// aya.sys - backend lecture/ecriture/allocation pour VALORANT
#include <ntifs.h>
#pragma warning(disable:4100)

#define AYA_DEV     L"\\Device\\AyaRw"
#define AYA_SYM     L"\\DosDevices\\AyaRw"
#define AYA_MAX     0x1000000
#define AYA_ALLOCMAX 0x4000000

#define AYA_READ    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define AYA_WRITE   CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define AYA_REGIONS CTL_CODE(FILE_DEVICE_UNKNOWN, 0x802, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define AYA_ALLOC   CTL_CODE(FILE_DEVICE_UNKNOWN, 0x803, METHOD_BUFFERED, FILE_ANY_ACCESS)

typedef struct { ULONG pid; ULONG pad; ULONG64 addr; ULONG64 buf; ULONG size; ULONG out; } REQ;
typedef struct { ULONG64 base; ULONG64 size; } REG;

static NTSTATUS rw_copy(REQ *r, BOOLEAN write)
{
    PEPROCESS p; KAPC_STATE apc; SIZE_T done = 0; NTSTATUS st;
    if (!r->pid || !r->size || r->size > AYA_MAX) return STATUS_INVALID_PARAMETER;
    st = PsLookupProcessByProcessId((HANDLE)(ULONG_PTR)r->pid, &p);
    if (!NT_SUCCESS(st)) return st;
    __try {
        if (write) ProbeForRead((PVOID)r->buf, r->size, 1);
        else       ProbeForWrite((PVOID)r->buf, r->size, 1);
        KeStackAttachProcess(p, &apc);
        st = MmCopyVirtualMemory(write ? PsGetCurrentProcess() : p,
                                 write ? (PVOID)r->buf : (PVOID)r->addr,
                                 write ? p : PsGetCurrentProcess(),
                                 write ? (PVOID)r->addr : (PVOID)r->buf,
                                 r->size, KernelMode, &done);
        KeUnstackDetachProcess(&apc);
    } __except (EXCEPTION_EXECUTE_HANDLER) { st = GetExceptionCode(); }
    ObDereferenceObject(p);
    return (NT_SUCCESS(st) && done == r->size) ? STATUS_SUCCESS : STATUS_UNSUCCESSFUL;
}

static NTSTATUS reg_scan(REQ *r, ULONG max, ULONG *count)
{
    PEPROCESS p; KAPC_STATE apc; NTSTATUS st; ULONG64 a = 0x10000; ULONG n = 0;
    MEMORY_BASIC_INFORMATION mbi; REG *out = (REG *)(PVOID)r;
    st = PsLookupProcessByProcessId((HANDLE)(ULONG_PTR)r->pid, &p);
    if (!NT_SUCCESS(st)) return st;
    KeStackAttachProcess(p, &apc);
    while (n < max && a < 0x7FFFFFFFFFFF) {
        SIZE_T got = 0;
        if (!NT_SUCCESS(ZwQueryVirtualMemory(ZwCurrentProcess(), (PVOID)a,
                MemoryBasicInformation, &mbi, sizeof(mbi), &got)) || !mbi.RegionSize) break;
        if (mbi.State == MEM_COMMIT && (mbi.Type == MEM_PRIVATE || mbi.Type == MEM_MAPPED)) {
            ULONG pr = mbi.Protect & 0xFF;
            if (pr == PAGE_READONLY || pr == PAGE_READWRITE || pr == PAGE_WRITECOPY ||
                pr == PAGE_EXECUTE_READ || pr == PAGE_EXECUTE_READWRITE ||
                pr == PAGE_EXECUTE_WRITECOPY) {
                out[n].base = (ULONG64)mbi.BaseAddress;
                out[n].size = (ULONG64)mbi.RegionSize;
                n++;
            }
        }
        a = (ULONG64)mbi.BaseAddress + mbi.RegionSize;
    }
    KeUnstackDetachProcess(&apc);
    ObDereferenceObject(p);
    *count = n;
    return STATUS_SUCCESS;
}

static NTSTATUS alloc_in(REQ *r, PVOID sb, ULONG outlen, ULONG *info)
{
    PEPROCESS p; KAPC_STATE apc; NTSTATUS st; PVOID base = NULL; SIZE_T sz;
    if (!r->pid || !r->size || r->size > AYA_ALLOCMAX || outlen < 8) return STATUS_INVALID_PARAMETER;
    sz = r->size;
    st = PsLookupProcessByProcessId((HANDLE)(ULONG_PTR)r->pid, &p);
    if (!NT_SUCCESS(st)) return st;
    KeStackAttachProcess(p, &apc);
    st = ZwAllocateVirtualMemory(ZwCurrentProcess(), &base, 0, &sz,
                                 MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    KeUnstackDetachProcess(&apc);
    ObDereferenceObject(p);
    if (!NT_SUCCESS(st)) return st;
    *(ULONG64 *)sb = (ULONG64)base;
    *info = 8;
    return STATUS_SUCCESS;
}

static NTSTATUS ioctl(PDEVICE_OBJECT dev, PIRP irp)
{
    PIO_STACK_LOCATION sp = IoGetCurrentIrpStackLocation(irp);
    ULONG code  = sp->Parameters.DeviceIoControl.IoControlCode;
    ULONG inlen = sp->Parameters.DeviceIoControl.InputBufferLength;
    ULONG outlen= sp->Parameters.DeviceIoControl.OutputBufferLength;
    PVOID sb = irp->AssociatedIrp.SystemBuffer;
    NTSTATUS st = STATUS_INVALID_PARAMETER;
    ULONG info = 0;

    if (inlen >= sizeof(REQ) && sb) {
        REQ r = *(REQ *)sb;
        switch (code) {
        case AYA_READ:    st = rw_copy(&r, FALSE); info = NT_SUCCESS(st) ? r.size : 0; break;
        case AYA_WRITE:   st = rw_copy(&r, TRUE);  info = NT_SUCCESS(st) ? r.size : 0; break;
        case AYA_REGIONS: if (outlen >= sizeof(REG)) st = reg_scan(&r, outlen / sizeof(REG), &info); break;
        case AYA_ALLOC:   st = alloc_in(&r, sb, outlen, &info); break;
        }
    }
    irp->IoStatus.Status = st;
    irp->IoStatus.Information = info;
    IoCompleteRequest(irp, IO_NO_INCREMENT);
    return st;
}

static NTSTATUS passthru(PDEVICE_OBJECT dev, PIRP irp)
{
    irp->IoStatus.Status = STATUS_SUCCESS;
    irp->IoStatus.Information = 0;
    IoCompleteRequest(irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}

static VOID unload(PDRIVER_OBJECT drv)
{
    UNICODE_STRING sym;
    RtlInitUnicodeString(&sym, AYA_SYM);
    IoDeleteSymbolicLink(&sym);
    IoDeleteDevice(drv->DeviceObject);
}

NTSTATUS DriverEntry(PDRIVER_OBJECT drv, PUNICODE_STRING reg)
{
    PDEVICE_OBJECT dev; UNICODE_STRING name, sym; NTSTATUS st;
    RtlInitUnicodeString(&name, AYA_DEV);
    RtlInitUnicodeString(&sym, AYA_SYM);
    st = IoCreateDevice(drv, 0, &name, FILE_DEVICE_UNKNOWN, 0, FALSE, &dev);
    if (!NT_SUCCESS(st)) return st;
    dev->Flags |= DO_BUFFERED_IO;
    st = IoCreateSymbolicLink(&sym, &name);
    if (!NT_SUCCESS(st)) { IoDeleteDevice(dev); return st; }
    drv->MajorFunction[IRP_MJ_CREATE] = passthru;
    drv->MajorFunction[IRP_MJ_CLOSE]  = passthru;
    drv->MajorFunction[IRP_MJ_DEVICE_CONTROL] = ioctl;
    drv->DriverUnload = unload;
    return STATUS_SUCCESS;
}