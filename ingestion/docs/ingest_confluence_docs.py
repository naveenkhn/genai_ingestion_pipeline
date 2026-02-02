import os
import json
import logging
from bs4 import BeautifulSoup
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import AzureOpenAIEmbeddings
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

load_dotenv()

# === Config ===
DRY_RUN = os.getenv("DRY_RUN", "False").lower() == "true"

# Create logs directory if it does not exist
if not os.path.exists("logs"):
    os.makedirs("logs")

if DRY_RUN:
    if not os.path.exists("dryrun"):
        os.makedirs("dryrun")

# Configure logging
log_filename = time.strftime("logs/docs_ingestion_%Y%m%d_%H%M%S.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)

# Suppress noisy logs from SDKs
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies").setLevel(logging.WARNING)
logging.getLogger("azure.search.documents").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
INDEX_NAME = os.getenv("AZURE_SEARCH_DOC_INDEX")
API_KEY = os.getenv("AZURE_SEARCH_API_KEY")

credential = AzureKeyCredential(API_KEY)

embeddings = AzureOpenAIEmbeddings(
    model="text-embedding-3-large",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY")
)

search_client = SearchClient(
    endpoint=SEARCH_ENDPOINT,
    index_name=INDEX_NAME,
    credential=credential
)

upload_lock = threading.Lock()

# === Helpers ===
def parse_html(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")
    return soup.get_text(separator="\n", strip=True)

def upload_documents_with_retries(docs, max_retries=3):
    for attempt in range(max_retries):
        try:
            with upload_lock:
                result = search_client.upload_documents(docs)
            return result
        except Exception as e:
            if attempt < max_retries - 1:
                logging.info(f"Upload attempt {attempt+1} failed with error: {e}. Retrying...")
                time.sleep(2 ** attempt)
            else:
                logging.error(f"Upload failed after {max_retries} attempts: {e}")
                raise

def embed_documents_with_retries(docs, max_retries=3):
    for attempt in range(max_retries):
        try:
            vectors = embeddings.embed_documents(docs)
            return vectors
        except Exception as e:
            if attempt < max_retries - 1:
                logging.info(f"Embedding attempt {attempt+1} failed with error: {e}. Retrying...")
                time.sleep(2 ** attempt)
            else:
                logging.error(f"Embedding failed after {max_retries} attempts: {e}")
                raise

def process_file(html_path, json_path, batch_size=100, dryrun_output=None):
    total_uploaded = 0
    batch_docs = []

    file = os.path.basename(html_path)
    page_id = file.split("_")[-1].replace(".html", "")

    with open(json_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    logging.info(f"Loaded metadata for {file}: {metadata.get('title', '')}")

    text = parse_html(html_path)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, chunk_overlap=0
    )
    chunks = splitter.split_text(text)
    logging.info(f"Created {len(chunks)} chunks for {file}")

    seen = set()
    unique_chunks = []
    skipped_count = 0
    for chunk in chunks:
        if chunk in seen:
            skipped_count += 1
            continue
        seen.add(chunk)
        unique_chunks.append(chunk)
    logging.info(f"Skipped {skipped_count} duplicate chunks for {file}")

    vectors = embed_documents_with_retries(unique_chunks)
    logging.info(f"Generated embeddings for {len(vectors)} chunks of {file}")

    for i, (chunk, vector) in enumerate(zip(unique_chunks, vectors)):
        batch_docs.append({
            "id": f"{page_id}_{i}",
            "content": chunk,
            "embedding": vector,
            "title": metadata["title"],
            "url": metadata["url"],
            "lastModified": metadata["lastModified"],
            "parentId": metadata["parentId"]
        })

        if len(batch_docs) >= batch_size:
            if DRY_RUN:
                truncated_docs = []
                for doc in batch_docs:
                    truncated_embedding = doc["embedding"][:2] if isinstance(doc["embedding"], (list, tuple)) else []
                    truncated_doc = doc.copy()
                    truncated_doc["embedding"] = truncated_embedding
                    truncated_docs.append(truncated_doc)
                dryrun_output.extend(truncated_docs)
                logging.info(f"Dry run: Prepared batch of {len(batch_docs)} docs for {file}. No upload performed.")
                total_uploaded += len(batch_docs)
                batch_docs.clear()
            else:
                logging.info(f"Batch size reached ({len(batch_docs)}) for {file}. Uploading batch...")
                result = upload_documents_with_retries(batch_docs)
                logging.info(f"Uploaded {len(result)} docs in current batch of {file} to Azure AI Search.")
                total_uploaded += len(result)
                batch_docs.clear()

    if batch_docs:
        if DRY_RUN:
            truncated_docs = []
            for doc in batch_docs:
                truncated_embedding = doc["embedding"][:2] if isinstance(doc["embedding"], (list, tuple)) else []
                truncated_doc = doc.copy()
                truncated_doc["embedding"] = truncated_embedding
                truncated_docs.append(truncated_doc)
            dryrun_output.extend(truncated_docs)
            logging.info(f"Dry run: Prepared final batch of {len(batch_docs)} docs for {file}. No upload performed.")
            total_uploaded += len(batch_docs)
            batch_docs.clear()
        else:
            logging.info(f"Uploading remaining {len(batch_docs)} docs in final batch for {file}...")
            result = upload_documents_with_retries(batch_docs)
            logging.info(f"Uploaded {len(result)} docs in final batch of {file} to Azure AI Search.")
            total_uploaded += len(result)
            batch_docs.clear()

    logging.info(f"Finished processing file {file}. Total documents uploaded: {total_uploaded}")
    return total_uploaded

def process_folder(folder_path, max_workers=8):
    logging.info(f"Starting processing folder: {folder_path}")

    file_pairs = []
    for root, _, files in os.walk(folder_path):   # recursive
        for file in files:
            if file.endswith(".html"):
                html_path = os.path.join(root, file)
                json_file = file.replace(".html", ".json")
                json_path = os.path.join(root, json_file)
                if os.path.exists(json_path):
                    file_pairs.append((html_path, json_path))

    total_uploaded = 0
    dryrun_output = [] if DRY_RUN else None
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {executor.submit(process_file, html, json_path, 100, dryrun_output): (html, json_path) for html, json_path in file_pairs}
        for future in as_completed(future_to_file):
            html, json_path = future_to_file[future]
            try:
                uploaded = future.result()
                total_uploaded += uploaded
            except Exception as e:
                logging.error(f"Error processing file pair ({html}, {json_path}): {e}")

    if DRY_RUN:
        dryrun_file_path = os.path.join("dryrun", "dryrun_output_docs.json")
        with open(dryrun_file_path, "w", encoding="utf-8") as f:
            json.dump(dryrun_output, f, indent=2)
        logging.info(f"Dry run complete. Prepared {len(dryrun_output)} documents. No uploads performed. Output written to {dryrun_file_path}.")

    logging.info(f"Finished processing folder. Total documents uploaded: {total_uploaded}")

# === Upload ===
if not DRY_RUN:
    try:
        count = search_client.get_document_count()
        logging.info(f"Connected to Azure AI Search index '{INDEX_NAME}'. Current document count: {count}")
    except Exception as e:
        logging.error(f"Failed to connect to Azure AI Search index '{INDEX_NAME}': {e}")
        exit(1)

folder = "./confluence_export/SPS/"
process_folder(folder)