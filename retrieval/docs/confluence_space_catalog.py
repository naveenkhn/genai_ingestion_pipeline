import sys
from atlassian import Confluence
from dotenv import load_dotenv
import os

load_dotenv()

CONFLUENCE_URL = os.getenv("CONFLUENCE_URL")
USERNAME = os.getenv("CONFLUENCE_USERNAME")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")

TARGETS = {
    "SITSPEC": [
    ]
}

# open log file
log_file = open("sps_tree.log", "w")

class DualWriter:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            if not s.closed:
                s.write(data)
                s.flush()
    def flush(self):
        for s in self.streams:
            if not s.closed:
                s.flush()

sys.stdout = DualWriter(sys.stdout, log_file)

confluence = Confluence(
    url=CONFLUENCE_URL,
    username=USERNAME,
    password=API_TOKEN
)

def print_tree(page_id, depth=0):
    page = confluence.get_page_by_id(page_id, expand="ancestors")
    title = page["title"]
    print(" " * (depth * 2) + f"- {title} (ID: {page_id})")

    children = confluence.get_page_child_by_type(page_id, type="page")
    for child in children:
        print_tree(child["id"], depth + 1)

if __name__ == "__main__":
    for space_key, titles in TARGETS.items():
        if not titles:
            print(f"\nListing all top-level pages and their hierarchies in space '{space_key}':\n")
            try:
                top_level_pages = confluence.get_all_pages_from_space(space_key, start=0, limit=50, status=None)
                for page in top_level_pages:
                    print(f"\n📘 {page['title']}:\n")
                    print_tree(page["id"])
            except Exception as e:
                print(f"\n⚠️ Error fetching pages in space '{space_key}': {e}")
        else:
            print(f"\nPage hierarchy for selected pages in space '{space_key}':\n")
            for title in titles:
                try:
                    page_id = confluence.get_page_id(space_key, title)
                    if page_id:
                        print(f"\n📘 {title}:\n")
                        print_tree(page_id)
                    else:
                        print(f"\n⚠️ Could not find page titled '{title}' in space '{space_key}'")
                except Exception as e:
                    print(f"\n⚠️ Error fetching page '{title}' in space '{space_key}': {e}")

    log_file.close()