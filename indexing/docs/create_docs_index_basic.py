from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    VectorSearchProfile,
    HnswAlgorithmConfiguration,
)
import os
from dotenv import load_dotenv

load_dotenv()

# --- Config ---
search_service_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")

# Give the name you want for the new document index
index_name = "docs-index-v1"

# API key for Azure Search
api_key = os.getenv("AZURE_SEARCH_API_KEY")

# Create client with API key credential
credential = AzureKeyCredential(api_key)
index_client = SearchIndexClient(endpoint=search_service_endpoint, credential=credential)

# --- Define Index (for documents) ---
fields = [
    SimpleField(name="id", type=SearchFieldDataType.String, key=True, sortable=True, filterable=True),
    SearchableField(name="title", type=SearchFieldDataType.String, filterable=True),
    SearchableField(name="content", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
    SearchField(
        name="embedding",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=3072,
        vector_search_profile_name="docsHnswProfile"
    ),
    SimpleField(name="url", type=SearchFieldDataType.String, filterable=True),
    SimpleField(name="lastModified", type=SearchFieldDataType.String, filterable=True, sortable=True),
    SimpleField(name="parentId", type=SearchFieldDataType.String, filterable=True)
]

vector_search = VectorSearch(
    algorithms=[HnswAlgorithmConfiguration(name="docsHnsw")],
    profiles=[VectorSearchProfile(name="docsHnswProfile", algorithm_configuration_name="docsHnsw")]
)

index = SearchIndex(
    name=index_name,
    fields=fields,
    vector_search=vector_search,
)

# --- Create Index ---
try:
    result = index_client.create_index(index)
    print(f"Index '{index_name}' created successfully")
except Exception as e:
    print(f"Error creating index: {e}")