import os
from dotenv import load_dotenv
from tree_sitter import Language, Parser

load_dotenv()
LANG_LIB = os.getenv("LANG_LIB")
if not LANG_LIB:
    raise RuntimeError("LANG_LIB is not set. Please define it in .env")

languages = ["cpp", "python", "java", "sql"]
snippets = {
    "cpp": b"int add(int a, int b) { return a + b; }",
    "python": b"def add(a, b):\n    return a + b",
    "java": b"class Demo { int add(int a, int b) { return a + b; } }",
    "sql": b"SELECT name, age FROM users WHERE age > 21;"
}

for lang in languages:
    try:
        lang_obj = Language(LANG_LIB, lang)
        parser = Parser()
        parser.set_language(lang_obj)
        tree = parser.parse(snippets[lang])
        print(f"{lang} parsed successfully:\n{tree.root_node.sexp()}\n")
    except Exception as e:
        print(f"{lang} {e}")