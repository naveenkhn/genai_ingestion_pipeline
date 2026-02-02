from tree_sitter import Language, Parser
FLUSH_BATCH_SIZE = 1000
import os
import json
import time
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import AzureOpenAIEmbeddings
from azure.search.documents import SearchClient
# from azure.identity import ClientSecretCredential
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
import hashlib
from datetime import datetime
import logging
from sql_symbol_extractor import extract_sql_symbols

load_dotenv()

os.makedirs("metadata", exist_ok=True)
os.makedirs("logs", exist_ok=True)
os.makedirs("dryrun", exist_ok=True)
log_filename = f"logs/ingestion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logger = logging.getLogger()
logger.setLevel(logging.INFO)

fh = logging.FileHandler(log_filename)
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))

ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("%(message)s"))

if logger.hasHandlers():
    logger.handlers.clear()

logger.addHandler(fh)
logger.addHandler(ch)

logger.info(f"Logging to {log_filename}")


# Dynamically load repositories from separate environment variables for each language
REPOS = []
def load_repos_by_language(env_var, language):
    repo_env = os.getenv(env_var)
    if not repo_env:
        logger.info(f"Warning: No {env_var} environment variable found.")
        return []
    repos = []
    for repo_name in [r.strip() for r in repo_env.split(",") if r.strip()]:
        repo_path = os.getenv(repo_name)
        if repo_path:
            repos.append((repo_name, repo_path, language))
        else:
            logger.info(f"Warning: Environment variable for repository path '{repo_name}' is missing. Skipping.")
    return repos

REPOS.extend(load_repos_by_language("CPP_REPOS", "cpp"))
REPOS.extend(load_repos_by_language("PYTHON_REPOS", "python"))
REPOS.extend(load_repos_by_language("JAVA_REPOS", "java"))
REPOS.extend(load_repos_by_language("SQL_REPOS", "sql"))

if not REPOS:
    logger.info("Warning: No repositories defined in CPP_REPOS/PYTHON_REPOS/JAVA_REPOS/SQL_REPOS. No repositories will be processed.")

LANGUAGE_LIB = "/Users/kumarn/workspace/build/my-languages.so"

LANGUAGES = {
    "cpp": Language(LANGUAGE_LIB, "cpp"),
    "python": Language(LANGUAGE_LIB, "python"),
    "java": Language(LANGUAGE_LIB, "java"),
    "sql": Language(LANGUAGE_LIB, "sql")
}

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY")
AZURE_SEARCH_CODE_INDEX = os.getenv("AZURE_SEARCH_CODE_INDEX")

# AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
# AZURE_SEARCH_CLIENT_ID = os.getenv("AZURE_SEARCH_CLIENT_ID")
# AZURE_SEARCH_CLIENT_SECRET = os.getenv("AZURE_SEARCH_CLIENT_SECRET")

AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

OPENGROK_BASE_URL_TEMPLATE = os.getenv("OPENGROK_BASE_URL_TEMPLATE")

DRY_RUN = False  # Set True to skip actual uploading and just output to file

# Environment variable to force full run (ignore last run metadata)
FORCE_FULL_RUN = os.getenv("FORCE_FULL_RUN", "false").lower() == "true"

# credential = ClientSecretCredential(
#     tenant_id=AZURE_TENANT_ID,
#     client_id=AZURE_SEARCH_CLIENT_ID,
#     client_secret=AZURE_SEARCH_CLIENT_SECRET
# )

embeddings = AzureOpenAIEmbeddings(
    azure_deployment=AZURE_OPENAI_DEPLOYMENT_NAME,
    model="text-embedding-3-large",
    api_key=AZURE_OPENAI_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=AZURE_OPENAI_API_VERSION,
)

search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_CODE_INDEX,
    credential=AzureKeyCredential(AZURE_SEARCH_API_KEY),
)

def get_node_text(node, code_bytes):
    return code_bytes[node.start_byte:node.end_byte].decode('utf-8')

def get_node_start_line(node):
    return node.start_point[0] + 1


def extract_functions_and_macros(root_node, code_bytes):
    results = []

    def add_symbol(symbol_type, node, signature_node=None, body_node=None):
        signature_text = get_node_text(signature_node if signature_node else node, code_bytes)
        body_text = get_node_text(body_node, code_bytes) if body_node else None

        symbol_name = None
        if symbol_type in ['function', 'prototype']:
            # Try to extract function name from declarator or identifier child
            if signature_node:
                # For function_definition, signature_node is function_declarator or similar
                for c in signature_node.children:
                    if c.type == 'identifier':
                        symbol_name = get_node_text(c, code_bytes)
                        break
            else:
                # fallback: search in node children
                for c in node.children:
                    if c.type == 'identifier':
                        symbol_name = get_node_text(c, code_bytes)
                        break
            if not symbol_name:
                # fallback: entire signature first word
                symbol_name = signature_text.split('(')[0].strip().split()[-1]
        elif symbol_type in ['class', 'struct']:
            # class_specifier or struct_specifier: name is identifier child
            for c in node.children:
                if c.type == 'type_identifier' or c.type == 'identifier':
                    symbol_name = get_node_text(c, code_bytes)
                    break
        elif symbol_type == 'macro':
            # preproc_def or preproc_function_def: name is first child after '#define'
            first_child = node.child_by_field_name('name')
            if first_child:
                symbol_name = get_node_text(first_child, code_bytes)
            else:
                # fallback: first token after '#define'
                tokens = signature_text.strip().split()
                if len(tokens) > 1:
                    symbol_name = tokens[1]
        elif symbol_type == 'enum':
            # enum_specifier: name is type_identifier child if exists
            for c in node.children:
                if c.type == 'type_identifier':
                    symbol_name = get_node_text(c, code_bytes)
                    break
            if not symbol_name:
                symbol_name = 'enum'
        elif symbol_type == 'macro_call':
            # call_expression with uppercase callee
            callee = None
            for c in node.children:
                if c.type == 'identifier':
                    callee = get_node_text(c, code_bytes)
                    break
            symbol_name = callee

        if not symbol_name:
            symbol_name = "<unknown>"

        # Only add start_line using helper function
        start_line = get_node_start_line(node)

        symbol_info = {
            "symbol": symbol_name,
            "type": symbol_type,
            "signature": signature_text,
            "start_line": start_line,
        }
        if body_text:
            symbol_info["body"] = body_text
        return symbol_info

    def traverse(node):
        # We look for:
        # function_definition (signature + body, with comments attached to signature)
        # class_specifier and struct_specifier (signature + body separately)
        # preproc_def, preproc_function_def (macros, but we skip adding them to results)
        # enum_specifier and enumerator_list
        # declaration (function prototypes)
        # call_expression with uppercase callee (macro-like calls, skip adding to results)

        if node.type == 'function_definition':
            # function_definition children: type, declarator, body
            declarator = None
            body = None
            for c in node.children:
                if c.type == 'function_declarator':
                    declarator = c
                elif c.type == 'compound_statement':
                    body = c
            symbol = add_symbol('function', node, signature_node=declarator, body_node=body)
            results.append(symbol)

        elif node.type == 'class_specifier' or node.type == 'struct_specifier':
            # signature: from start to body start (type + identifier + base classes)
            # body: compound_statement or field_declaration_list child
            # Usually, the body is a 'field_declaration_list' child
            signature_end_byte = None
            body_node = None
            for c in node.children:
                if c.type == 'field_declaration_list':
                    body_node = c
                    signature_end_byte = c.start_byte
                    break
            if body_node:
                signature_text = code_bytes[node.start_byte:signature_end_byte].decode('utf-8')
                body_text = get_node_text(body_node, code_bytes)
                symbol_name = None
                for c in node.children:
                    if c.type == 'type_identifier' or c.type == 'identifier':
                        symbol_name = get_node_text(c, code_bytes)
                        break
                if not symbol_name:
                    symbol_name = "<unknown>"
                # Add start_line info only
                start_line = get_node_start_line(node)
                results.append({
                    "symbol": symbol_name,
                    "type": node.type.replace('_specifier',''),
                    "signature": signature_text,
                    "body": body_text,
                    "start_line": start_line,
                })
            else:
                # No body found, treat whole node as signature
                symbol = add_symbol(node.type.replace('_specifier',''), node)
                results.append(symbol)

        elif node.type == 'preproc_def' or node.type == 'preproc_function_def':
            # Macro nodes: do not append to results, but parse for completeness if needed
            _ = add_symbol('macro', node)
            # Do not append to results

        elif node.type == 'enum_specifier':
            # signature up to enumerator_list
            enumerator_list_node = None
            for c in node.children:
                if c.type == 'enumerator_list':
                    enumerator_list_node = c
                    break
            if enumerator_list_node:
                signature_text = code_bytes[node.start_byte:enumerator_list_node.start_byte].decode('utf-8')
                body_text = get_node_text(enumerator_list_node, code_bytes)
                symbol_name = None
                for c in node.children:
                    if c.type == 'type_identifier':
                        symbol_name = get_node_text(c, code_bytes)
                        break
                if not symbol_name:
                    symbol_name = "enum"
                # Add start_line info only
                start_line = get_node_start_line(node)
                results.append({
                    "symbol": symbol_name,
                    "type": "enum",
                    "signature": signature_text,
                    "body": body_text,
                    "start_line": start_line,
                })
            else:
                symbol = add_symbol('enum', node)
                results.append(symbol)

        elif node.type == 'declaration':
            # Could be function prototype if it has a function_declarator child
            has_func_decl = False
            func_decl_node = None
            for c in node.children:
                if c.type == 'function_declarator':
                    has_func_decl = True
                    func_decl_node = c
                    break
            if has_func_decl:
                symbol = add_symbol('prototype', node, signature_node=func_decl_node)
                results.append(symbol)

        elif node.type == 'call_expression':
            # Check if callee is uppercase (macro-like call)
            callee = None
            for c in node.children:
                if c.type == 'identifier':
                    callee = get_node_text(c, code_bytes)
                    break
            if callee and callee.isupper():
                # Macro-like call: do not append to results, but parse for completeness if needed
                _ = add_symbol('macro_call', node)
                # Do not append to results

        # Recurse children
        for child in node.children:
            traverse(child)

    traverse(root_node)
    return results

def process_file(filepath, repo_name, language):
    logger.info(f"Starting processing file: {filepath}")
    start_time = time.time()
    with open(filepath, "rb") as f:
        code_bytes = f.read()

    if language == "sql":
        # Use the SQL-specific extractor (DDL only; INSERTs are ignored there)
        parser = Parser()
        parser.set_language(LANGUAGES[language])
        tree = parser.parse(code_bytes)
        root_node = tree.root_node
        symbols = extract_sql_symbols(root_node, code_bytes)
    else:
        parser = Parser()
        parser.set_language(LANGUAGES[language])
        tree = parser.parse(code_bytes)
        root_node = tree.root_node
        symbols = extract_functions_and_macros(root_node, code_bytes)
    logger.info(f"Extracted {len(symbols)} symbols from file: {filepath}")
    last_modified = os.path.getmtime(filepath)
    last_modified_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_modified))
    for sym in symbols:
        sym["file_name"] = os.path.basename(filepath)
        sym["file_path"] = filepath
        sym["lastModified"] = last_modified_str
        sym["repo"] = repo_name
        sym["language"] = language
    logger.info(f"Finished processing file: {filepath}")
    elapsed = time.time() - start_time
    logger.info(f"Time taken for file {filepath}: {elapsed:.2f} seconds")
    return symbols

def process_folder(folder_path, repo_name, language):
    logger.info(f"Starting processing folder: {folder_path} (repo: {repo_name})")
    EXTENSIONS = {
        "cpp": (".cpp", ".hpp", ".h"),
        "python": (".py",),
        "java": (".java",),
        "sql": (".sql",)
    }
    exts = EXTENSIONS.get(language, ())
    all_symbols = []
    for root, dirs, files in os.walk(folder_path):
        for fname in files:
            if fname.endswith(exts):
                filepath = os.path.join(root, fname)
                logger.info(f"Processing file: {filepath}")
                try:
                    syms = process_file(filepath, repo_name, language)
                    all_symbols.extend(syms)
                except Exception as e:
                    logger.info(f"Error processing file {filepath}: {e}")
                    all_symbols.append({
                        "file_name": fname,
                        "file_path": filepath,
                        "error": str(e),
                        "repo": repo_name
                    })
                logger.info(f"Completed file: {filepath}")
    logger.info(f"Completed processing folder: {folder_path} with total symbols: {len(all_symbols)}")
    return all_symbols

def chunk_symbol(symbol):
    logger.info(f"Chunking symbol: {symbol.get('symbol', '<unknown>')} of type {symbol.get('type', '')}")
    # Compose the text to chunk: comments (if any), then signature, then body
    comments = symbol.get("comments", "")
    text_to_chunk = ""
    if comments:
        text_to_chunk += comments.strip() + "\n"
    text_to_chunk += symbol.get("signature", "")
    if symbol.get("body"):
        text_to_chunk += "\n" + symbol["body"]

    # For macros and enums: do not chunk, just return as a single chunk
    if symbol.get("type") in ("macro", "enum"):
        return [text_to_chunk]
    # For other types: chunk as usual, with no overlap
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
    chunks = splitter.split_text(text_to_chunk)
    return chunks

def compute_doc_id(symbol, chunk):
    """Generate deterministic ID based on repo, file, symbol, and chunk."""
    data = f"{symbol.get('repo','')}_{symbol.get('file_path','')}_{symbol.get('symbol','')}_{chunk}"
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

def load_last_run_metadata(repo_name):
    path = f"metadata/{repo_name}_last_run.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}

def save_last_run_metadata(repo_name, metadata_dict):
    path = f"metadata/{repo_name}_last_run.json"
    with open(path, "w") as f:
        json.dump(metadata_dict, f, indent=4)

def generate_document(chunk, symbol):
    logger.info(f"Generating document for chunk of symbol: {symbol.get('symbol', '<unknown>')}")
    doc_id = compute_doc_id(symbol, chunk)

    # Use repo name from symbol, fallback to empty string for backward compatibility
    repo = symbol.get("repo", "")
    file_path = symbol.get("file_path", "")
    # Compute rel_path relative to repo src root
    # Try to find "{repo}/" in the file_path, else fallback to basename
    rel_path = None
    repo_search = f"{repo}/"
    if repo_search in file_path:
        rel_path = file_path.split(repo_search, 1)[-1]
    else:
        rel_path = os.path.basename(file_path)
    relative_path = rel_path
    # Normalize OpenGrok URL for line-accurate linking
    start_line = symbol.get("start_line", None)
    if start_line:
        opengrok_url = f"{OPENGROK_BASE_URL_TEMPLATE.rstrip('/')}/{repo}/{relative_path.lstrip('/')}#{start_line}"
    else:
        # fallback to file start if we somehow missed line info
        opengrok_url = f"{OPENGROK_BASE_URL_TEMPLATE.rstrip('/')}/{repo}/{relative_path.lstrip('/')}"

    doc = {
        "id": doc_id,
        "code": chunk,
        "symbol": symbol.get("symbol", ""),
        "type": symbol.get("type", ""),
        "signature": symbol.get("signature", ""),
        "filePath": rel_path,
        "lastModified": symbol.get("lastModified", ""),
        "repo": repo,
        "opengrokUrl": opengrok_url,
        "language": symbol.get("language", ""),
    }
    return doc

def upload_documents(docs, repo_name):
    logger.info(f"[UPLOAD] Would upload {len(docs)} docs (DRY_RUN={DRY_RUN})")
    # Always write a single file per repo per run; no appends, no mixing
    # Truncate embedding to first 3 elements + ["..."] for readability in dryrun JSON
    trimmed_docs = []
    for doc in docs:
        d = dict(doc)
        if "embedding" in d and isinstance(d["embedding"], list) and len(d["embedding"]) > 3:
            d["embedding"] = d["embedding"][:3] + ["..."]
        trimmed_docs.append(d)
    dryrun_file = f"dryrun/dryrun_output_{repo_name}.json"
    with open(dryrun_file, "w", encoding="utf-8") as f:
        json.dump(trimmed_docs, f, indent=4)
    logger.info(f"Dry run output: wrote {len(trimmed_docs)} documents for {repo_name} to {dryrun_file}")

    if DRY_RUN:
        return

    # Check if index exists and is accessible
    try:
        search_client.get_document_count()
    except Exception as e:
        logger.info(f"Failed to connect to search index: {e}")
        return

    batch_size = 500
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i+batch_size]
        attempts = 0
        max_attempts = 3
        while attempts < max_attempts:
            try:
                result = search_client.merge_or_upload_documents(documents=batch)
                logger.info(f"Uploaded batch of {len(batch)} documents, status: {result[0].status_code}")
                break
            except Exception as e:
                attempts += 1
                if attempts < max_attempts:
                    logger.info(f"[RETRY] Failed to upload batch (attempt {attempts}/{max_attempts}) for repo {repo_name}: {e}")
                    logger.info(f"[WAIT] Waiting 30 seconds before retrying...")
                    time.sleep(30)
                else:
                    logger.info(f"[FAIL] Failed to upload batch after {max_attempts} attempts for repo {repo_name}: {e}")

if __name__ == "__main__":
    batch_size = 20
    from collections import defaultdict

    for repo_tuple in REPOS:
        # repo_tuple: (repo_name, repo_path, language)
        repo_name, repo_path, language = repo_tuple
        logger.info(f"\n========== Processing repository: {repo_name} [{language}] ==========")

        # a fresh buffer per repo so dry-run JSON never mixes repos
        doc_buffer = []

        # Load last run metadata for this repo, or ignore if FORCE_FULL_RUN
        if FORCE_FULL_RUN:
            last_meta = {}
            logger.info(f"[FORCE] Ignoring last run metadata for repo: {repo_name}")
        else:
            last_meta = load_last_run_metadata(repo_name)
        current_meta = {}
        new_or_changed_files = 0
        skipped_files = 0

        results = process_folder(repo_path, repo_name, language)

        file_to_syms = defaultdict(list)
        for sym in results:
            file_to_syms[sym.get("file_path", "<unknown>")].append(sym)

        for file_path, syms in file_to_syms.items():
            # Check if the file has changed since last run
            last_modified = os.path.getmtime(file_path)
            last_modified_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_modified))
            if file_path in last_meta and last_meta[file_path] == last_modified_str:
                skipped_files += 1
                logger.info(f"[SKIP] {file_path} (unchanged)")
                continue

            current_meta[file_path] = last_modified_str
            new_or_changed_files += 1

            file_start_time = time.time()
            symbol_chunk_info = []
            for sym in syms:
                chunks = chunk_symbol(sym)
                for chunk in chunks:
                    symbol_chunk_info.append((sym, chunk))

            all_chunks = [chunk for (_, chunk) in symbol_chunk_info]
            if not all_chunks:
                continue

            logger.info(f"Generating embeddings for {len(all_chunks)} chunks from file: {file_path}")
            embeddings_for_chunks = []
            for i in range(0, len(all_chunks), batch_size):
                batch = all_chunks[i:i+batch_size]
                logger.info(f"Embedding batch of {len(batch)} chunks for file: {file_path}")
                batch_embeddings = embeddings.embed_documents(batch)
                logger.info(f"Received {len(batch_embeddings)} embeddings for file: {file_path}")
                embeddings_for_chunks.extend(batch_embeddings)

            assert len(embeddings_for_chunks) == len(symbol_chunk_info)

            docs_for_file = []
            for (sym, chunk), embedding in zip(symbol_chunk_info, embeddings_for_chunks):
                doc = generate_document(chunk, sym)
                doc["embedding"] = embedding
                docs_for_file.append(doc)

            doc_buffer.extend(docs_for_file)
            logger.info(f"Prepared {len(symbol_chunk_info)} documents for file: {file_path}")
            file_elapsed = time.time() - file_start_time
            logger.info(f"Total time for embeddings + docs for file {file_path}: {file_elapsed:.2f} seconds")

            # Only trigger mid-run uploads for NON dry-run mode (batching to the service)
            if not DRY_RUN and len(doc_buffer) >= FLUSH_BATCH_SIZE:
                upload_documents(doc_buffer, repo_name)
                doc_buffer.clear()

        # After processing all files in this repo, save metadata and flush whatever is in the buffer
        save_last_run_metadata(repo_name, current_meta)
        logger.info(f"[SUMMARY] Repo={repo_name}, new/changed={new_or_changed_files}, skipped={skipped_files}")

        if doc_buffer:
            upload_documents(doc_buffer, repo_name)
            doc_buffer.clear()

    logger.info("=== Ingestion run completed ===")
    logger.info("Ingestion run complete. Check logs/ and metadata/ for details.")