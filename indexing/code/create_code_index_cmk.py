from azure.identity import ClientSecretCredential
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
    SearchResourceEncryptionKey,
)
import os
from dotenv import load_dotenv

load_dotenv()

# --- Config ---
search_service_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")

# Give the name you want for the new index
index_name = "code-index"

# Service principal credentials
tenant_id = os.getenv("AZURE_TENANT_ID")
client_id = os.getenv("AZURE_SEARCH_CLIENT_ID")
client_secret = os.getenv("AZURE_SEARCH_CLIENT_SECRET")

# Define Key Vault values
key_vault_uri = os.getenv("AZURE_KEY_VAULT_URI")
key_name = os.getenv("AZURE_KEY_VAULT_NAME")
key_version = os.getenv("AZURE_KEY_VAULT_VERSION")

# Create credential + client
credential = ClientSecretCredential(tenant_id, client_id, client_secret)
index_client = SearchIndexClient(endpoint=search_service_endpoint, credential=credential)

# --- Define Index (for code) ---
fields = [
    SimpleField(name="id", type=SearchFieldDataType.String, key=True, sortable=True, filterable=True),
    SearchableField(name="repo", type=SearchFieldDataType.String, filterable=True, facetable=True),
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

vector_search = VectorSearch(
    algorithms=[HnswAlgorithmConfiguration(name="codeHnsw")],
    profiles=[VectorSearchProfile(name="codeHnswProfile", algorithm_configuration_name="codeHnsw")]
)

encryption_key = SearchResourceEncryptionKey(
    key_name=key_name,
    key_version=key_version,
    vault_uri=key_vault_uri,
    identity_client_id=client_id
)

index = SearchIndex(
    name=index_name,
    fields=fields,
    vector_search=vector_search,
    encryption_key=encryption_key,
)

# --- Create Index ---
try:
    result = index_client.create_index(index)
    print(f"Index '{index_name}' created successfully with CMK encryption")
except Exception as e:
    print(f"Error creating index: {e}")