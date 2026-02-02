import os
from dotenv import load_dotenv
import ctypes
from ctypes import c_void_p, c_char_p

load_dotenv()
LANG_LIB = os.getenv("LANG_LIB")
if not LANG_LIB:
    raise RuntimeError("LANG_LIB is not set. Please define it in .env")

# Load the shared object manually
lib = ctypes.CDLL(LANG_LIB)

# Define return types
lib.tree_sitter_cpp.restype = c_void_p
lib.tree_sitter_python.restype = c_void_p
lib.tree_sitter_java.restype = c_void_p
lib.tree_sitter_sql.restype = c_void_p

# Test language function pointers
langs = {
    "cpp": lib.tree_sitter_cpp,
    "python": lib.tree_sitter_python,
    "java": lib.tree_sitter_java,
    "sql": lib.tree_sitter_sql,
}

for name, func in langs.items():
    try:
        print(f"{name} loaded:", func)
    except Exception as e:
        print(f"{name} {e}")