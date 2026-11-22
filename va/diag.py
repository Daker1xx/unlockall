"""diag.py - inspecte PdAPI pour recuperer l'URL reelle du loadout."""
import inspect
import vcapi

print("== methodes publiques PdAPI ==")
print([m for m in dir(vcapi.PdAPI) if not m.startswith("_")])

print("\n== source de la classe ==")
print(inspect.getsource(vcapi.PdAPI))