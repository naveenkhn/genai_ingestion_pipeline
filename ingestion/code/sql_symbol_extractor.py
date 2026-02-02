# --- NEW: SQL extractor ---
def extract_sql_symbols(root, code_bytes, repo_name=None, file_path=None):
    def text(n): return code_bytes[n.start_byte:n.end_byte].decode('utf-8')
    def sl(n): return n.start_point[0] + 1
    def el(n): return n.end_point[0] + 1

    MAX_DDL_LEN = 4000  # safeguard: truncate DDLs before embedding

    out = []

    def add(obj):
        # Always add start_line if not present
        if "start_line" not in obj and "startLine" in obj:
            obj["start_line"] = obj["startLine"]
            del obj["startLine"]
        if "start_line" not in obj:
            obj["start_line"] = None
        # Remove end_line if present
        if "end_line" in obj:
            del obj["end_line"]
        out.append(obj)

    def get_ident_after_keyword(txt, keyword):
        # crude but robust for naming: CREATE INDEX <name> ON ...
        parts = txt.split()
        try:
            i = [p.upper() for p in parts].index(keyword)
            return parts[i+1]
        except Exception:
            return None

    def truncate_ddl(ddl):
        if ddl and len(ddl) > MAX_DDL_LEN:
            return ddl[:MAX_DDL_LEN] + "\n-- [truncated]"
        return ddl

    def visit(n, parent_table=None):
        t = n.type

        # CREATE TABLE
        if t == "create_table_statement":
            ddl = text(n)
            if len(ddl) > MAX_DDL_LEN:
                ddl = truncate_ddl(ddl)
            # name: child field 'name' or first identifier after CREATE TABLE
            name = None
            for c in n.children:
                if c.type in ("identifier", "object_name", "table_name"):
                    name = text(c)
                    break
            if not name:
                name = get_ident_after_keyword(ddl, "TABLE")
            # Find the parenthesized column/constraint block for body
            open_paren = ddl.find('(')
            close_paren = ddl.rfind(')')
            if open_paren != -1 and close_paren != -1 and close_paren > open_paren:
                # signature: text before first '(' (not including '(')
                signature = ddl[:open_paren].rstrip()
                # body: text inside the first matching parentheses
                body = ddl[open_paren+1:close_paren]
            else:
                signature = ddl
                body = ""
            add({
                "symbol": name or "<table>",
                "type": "table",
                "signature": signature,
                "body": body,
                "start_line": sl(n),
            })

        # CREATE INDEX / UNIQUE INDEX
        elif t in ("create_index_statement", "create_unique_index_statement"):
            ddl = text(n)
            if len(ddl) > MAX_DDL_LEN:
                ddl = truncate_ddl(ddl)
            idx = None
            tbl = None
            cols = []
            # name: after CREATE [UNIQUE] INDEX
            idx = get_ident_after_keyword(ddl, "INDEX") or "<index>"
            # table + columns (best effort via ON <table>(...))
            up = ddl.upper()
            if " ON " in up:
                try:
                    after_on = ddl[up.index(" ON ") + 4:]
                    tbl = after_on.split("(")[0].strip()
                    col_part = after_on.split("(", 1)[1].split(")", 1)[0]
                    cols = [c.strip().strip(",") for c in col_part.split(",")]
                    # strip ASC/ DESC
                    cols = [c.split()[0] for c in cols if c]
                except Exception:
                    pass
            # For index/sequence/constraint: set signature=ddl, body=""
            add({
                "symbol": idx,
                "type": "index",
                "signature": ddl,
                "body": "",
                "table": tbl,
                "columns": cols,
                "unique": (t == "create_unique_index_statement"),
                "start_line": sl(n),
            })

        # CREATE SEQUENCE
        elif t == "create_sequence_statement":
            ddl = text(n)
            if len(ddl) > MAX_DDL_LEN:
                ddl = truncate_ddl(ddl)
            name = get_ident_after_keyword(ddl, "SEQUENCE") or "<sequence>"
            add({
                "symbol": name,
                "type": "sequence",
                "signature": ddl,
                "body": "",
                "start_line": sl(n),
            })

        # ALTER TABLE ... ADD CONSTRAINT (PK/FK/UNIQUE/CHECK)
        elif t == "alter_table_statement":
            ddl = text(n)
            if len(ddl) > MAX_DDL_LEN:
                ddl = truncate_ddl(ddl)
            tbl = None
            tbl = get_ident_after_keyword(ddl, "TABLE")
            kind = None
            cname = None
            cols = []
            up = ddl.upper()
            if " CONSTRAINT " in up:
                try:
                    after = ddl[up.index(" CONSTRAINT ") + len(" CONSTRAINT "):]
                    cname = after.split()[0]
                except Exception:
                    pass
            if " PRIMARY KEY " in up: kind = "PRIMARY KEY"
            elif " FOREIGN KEY " in up: kind = "FOREIGN KEY"
            elif " UNIQUE " in up: kind = "UNIQUE"
            elif " CHECK " in up: kind = "CHECK"
            try:
                paren = ddl[ddl.index("(")+1:ddl.index(")")]
                cols = [c.strip() for c in paren.split(",")]
            except Exception:
                pass
            # For constraint: set signature=ddl, body=""
            add({
                "symbol": cname or (tbl + "_constraint" if tbl else "<constraint>"),
                "type": "constraint",
                "signature": ddl,
                "body": "",
                "table": tbl,
                "kind": kind,
                "columns": cols,
                "start_line": sl(n),
            })

        # DML — group INSERTs by table into blocks
        elif t == "insert_statement":
            pass

        # Recurse
        for c in n.children:
            visit(c)

    visit(root)

    return out