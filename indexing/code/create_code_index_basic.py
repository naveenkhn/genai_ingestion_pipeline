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
index_name = os.getenv("AZURE_SEARCH_CODE_INDEX")
api_key = os.getenv("AZURE_SEARCH_API_KEY")

# --- Create Search Client using API Key ---
index_client = SearchIndexClient(
    endpoint=search_service_endpoint,
    credential=AzureKeyCredential(api_key)
)

# --- Define Index (for code) ---
fields = [
    SimpleField(name="id", type=SearchFieldDataType.String, key=True, sortable=True, filterable=True),
    SearchableField(name="repo", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchableField(name="language", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchableField(name="filePath", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchableField(name="symbol", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SimpleField(name="startLine", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
    SimpleField(name="endLine", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
    SimpleField(name="lastModified", type=SearchFieldDataType.String, filterable=True, sortable=True),
    SearchableField(name="code", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
    SearchableField(name="signature", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
    SearchableField(name="body", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
    SearchableField(name="type", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchField(
        name="embedding",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=3072,
        vector_search_profile_name="codeHnswProfile"
    ),
    SearchableField(name="opengrokUrl", type=SearchFieldDataType.String, filterable=True)
]

# --- Vector Search Configuration ---
vector_search = VectorSearch(
    algorithms=[HnswAlgorithmConfiguration(name="codeHnsw")],
    profiles=[VectorSearchProfile(name="codeHnswProfile", algorithm_configuration_name="codeHnsw")]
)

# --- Create Index ---
index = SearchIndex(
    name=index_name,
    fields=fields,
    vector_search=vector_search
)

try:
    result = index_client.create_index(index)
    print(f"Index '{index_name}' created successfully using API key authentication.")
except Exception as e:
    print(f"Error creating index: {e}")