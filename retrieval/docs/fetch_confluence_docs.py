from dotenv import load_dotenv
load_dotenv()
from atlassian import Confluence
import os
import json
import re
import time
import logging
from requests.exceptions import Timeout, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed

CONFLUENCE_URL = os.getenv("CONFLUENCE_URL")
USERNAME = os.getenv("CONFLUENCE_USERNAME")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")

# ==========================================================
# Configure only the spaces and page IDs you want to fetch
# ==========================================================
TARGETS = {
    "SITSPEC": [
        # Fetch Everything
    ],
    "SPS": [
        "2255101759",  # Assessments and studies
        "2255101749"   # Documentation corner
    ]
}

OUTPUT_DIR = "confluence_export"
os.makedirs(OUTPUT_DIR, exist_ok=True)

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_file_path = os.path.join(LOG_DIR, "fetch_docs.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Suppress INFO logs from the Atlassian library
logging.getLogger("atlassian").setLevel(logging.WARNING)

def safe_filename(s):
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', s)

# --------------------------------------------------------------------

confluence = Confluence(
    url=CONFLUENCE_URL,
    username=USERNAME,
    password=API_TOKEN,
    timeout=300
)

# --------------------------------------------------------------------

def save_page_content(page, folder_path, space_key):
    page_id = page['id']
    title = page['title']
    safe_title = safe_filename(title)

    json_path = os.path.join(folder_path, f"{safe_title}_{page_id}.json")
    html_path = os.path.join(folder_path, f"{safe_title}_{page_id}.html")

    existing_last_modified = None
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f_json:
                existing_meta = json.load(f_json)
                existing_last_modified = existing_meta.get("lastModified")
        except Exception as e:
            logging.warning(f"Could not read {json_path}: {e}")

    # Retry logic
    max_retries = 3
    delay = 2
    page_content = None
    for attempt in range(max_retries):
        try:
            page_content = confluence.get_page_by_id(page_id, expand='body.export_view,version,ancestors')
            break
        except (Timeout, HTTPError) as e:
            logging.error(f"Error fetching page ID {page_id} on attempt {attempt+1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                metadata = {
                    "id": page_id,
                    "title": title,
                    "space": space_key,
                    "url": f"{CONFLUENCE_URL}/spaces/{space_key}/pages/{page_id}",
                    "lastModified": None,
                    "error": f"Failed after {max_retries} attempts: {str(e)}"
                }
                with open(json_path, 'w', encoding='utf-8') as f_json:
                    json.dump(metadata, f_json, indent=2)
                logging.info(f"Skipped page ID {page_id} titled '{title}' due to repeated errors.")
                return None
        except Exception as e:
            logging.error(f"Unexpected error fetching page ID {page_id}: {e}")
            metadata = {
                "id": page_id,
                "title": title,
                "space": space_key,
                "url": f"{CONFLUENCE_URL}/spaces/{space_key}/pages/{page_id}",
                "lastModified": None,
                "error": f"Unexpected error: {str(e)}"
            }
            with open(json_path, 'w', encoding='utf-8') as f_json:
                json.dump(metadata, f_json, indent=2)
            logging.info(f"Skipped page ID {page_id} titled '{title}' due to unexpected error.")
            return None

    # Compare timestamps to skip unchanged pages
    page_last_modified = page_content.get('version', {}).get('when')
    if existing_last_modified and page_last_modified and existing_last_modified == page_last_modified:
        logging.info(f"Page ID {page_id} titled '{title}' already up-to-date, skipping.")
        return "skipped"

    html_content = page_content.get('body', {}).get('export_view', {}).get('value', '')

    with open(html_path, 'w', encoding='utf-8') as f_html:
        f_html.write(html_content)

    metadata = {
        "id": page_id,
        "title": title,
        "space": space_key,
        "url": f"{CONFLUENCE_URL}/spaces/{space_key}/pages/{page_id}",
        "lastModified": page_last_modified,
        "parentId": page_content['ancestors'][-1]['id'] if page_content.get('ancestors') else None
    }

    with open(json_path, 'w', encoding='utf-8') as f_json:
        json.dump(metadata, f_json, indent=2)

    logging.info(f"Exported page ID {page_id} titled '{title}'")
    return "exported"

# --------------------------------------------------------------------

def fetch_space(space_key, page_ids):
    space_folder = os.path.join(OUTPUT_DIR, space_key)
    os.makedirs(space_folder, exist_ok=True)

    folder_map = {}

    total_exported = 0
    total_skipped = 0
    total_failed = 0

    if not page_ids:
        logging.info(f"\nFetching starting from home page of space {space_key}")
        try:
            space = confluence.get_space(space_key, expand='homepage')
            home_id = space['homepage']['id']
            home_page = confluence.get_page_by_id(home_id, expand='version,ancestors')
            current_level_pages = [home_page]
            folder_map[home_id] = space_folder
            logging.debug(f"Detected home page '{home_page['title']}' with ID {home_id} for space {space_key}")
        except Exception as e:
            logging.error(f"Error fetching home page for space {space_key}: {e}")
            return (space_key, total_exported, total_skipped, total_failed)
    else:
        current_level_pages = []
        logging.info(f"\nFetching specified root pages in space {space_key}: {', '.join(page_ids)}")
        for pid in page_ids:
            try:
                page = confluence.get_page_by_id(pid, expand='version,ancestors')
                if not page:
                    logging.warning(f"SKIPPED target ID {pid} in space {space_key}: not found or access denied (get_page_by_id returned None).")
                    continue
                if 'space' in page and page['space'].get('key') and page['space']['key'] != space_key:
                    logging.warning(f"Target page ID {pid} belongs to space '{page['space']['key']}', not '{space_key}'. Proceeding anyway.")
                logging.info(f"Resolved target ID {pid} → title '{page.get('title','?')}'")
                current_level_pages.append(page)
                folder_map[pid] = space_folder
            except HTTPError as he:
                logging.error(f"HTTP error fetching page ID {pid} in {space_key}: {he}")
                total_failed += 1
                continue
            except Exception as e:
                logging.error(f"Error fetching page ID {pid} in {space_key}: {e}")
                total_failed += 1

    while current_level_pages:
        next_level_pages = []
        futures = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            for page in current_level_pages:
                title = page['title']
                page_id = page['id']
                parent_id = page.get("parent_id")
                if parent_id and parent_id in folder_map:
                    parent_folder = folder_map[parent_id]
                else:
                    parent_folder = space_folder
                safe_title = safe_filename(title)
                page_folder = os.path.join(parent_folder, f"{safe_title}_{page_id}")
                os.makedirs(page_folder, exist_ok=True)
                folder_map[page_id] = page_folder
                logging.debug(f"Processing page '{title}' (ID {page_id}) with parent_id {parent_id} in folder {page_folder}")
                futures[executor.submit(save_page_content, page, page_folder, space_key)] = (page, page_folder)

            for future in as_completed(futures):
                page, page_folder = futures[future]
                page_id = page['id']
                try:
                    result = future.result()
                    if result == "exported":
                        total_exported += 1
                    elif result == "skipped":
                        total_skipped += 1
                    else:
                        total_failed += 1
                    if result:
                        try:
                            children = confluence.get_page_child_by_type(page_id, type='page')
                            if children:
                                for child in children:
                                    child_id = child['id']
                                    child_title = child['title']
                                    child_safe_title = safe_filename(child_title)
                                    child_folder = os.path.join(page_folder, f"{child_safe_title}_{child_id}")
                                    os.makedirs(child_folder, exist_ok=True)
                                    child_dict = {
                                        "id": child_id,
                                        "title": child_title,
                                        "parent_id": page_id
                                    }
                                    folder_map[child_id] = child_folder
                                    next_level_pages.append(child_dict)
                        except Exception as e:
                            logging.error(f"Error fetching children of page ID {page_id}: {e}")
                except Exception as e:
                    logging.error(f"Error processing page ID {page_id}: {e}")
                    total_failed += 1

        # Convert dicts in next_level_pages to full page dicts by fetching their details
        detailed_next_level_pages = []
        for child_dict in next_level_pages:
            try:
                child_page = confluence.get_page_by_id(child_dict['id'], expand='version,ancestors')
                if child_page:
                    # Add parent_id to child_page for folder determination in next iteration
                    child_page['parent_id'] = child_dict['parent_id']
                    detailed_next_level_pages.append(child_page)
                else:
                    logging.info(f"Child page ID {child_dict['id']} not found when expanding next level.")
            except Exception as e:
                logging.error(f"Error fetching child page ID {child_dict['id']}: {e}")
                total_failed += 1

        current_level_pages = detailed_next_level_pages

    return (space_key, total_exported, total_skipped, total_failed)

# --------------------------------------------------------------------

def main():
    summary = []
    for space_key, page_ids in TARGETS.items():
        result = fetch_space(space_key, page_ids)
        summary.append(result)
    logging.info("\nFinal summary of all spaces:")
    for space_key, exported, skipped, failed in summary:
        logging.info(f"Space '{space_key}': Exported={exported}, Skipped={skipped}, Failed={failed}")

# --------------------------------------------------------------------

if __name__ == "__main__":
    main()