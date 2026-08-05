from opensearchpy import AsyncOpenSearch


def create_opensearch_client(opensearch_url: str) -> AsyncOpenSearch:
    return AsyncOpenSearch(hosts=[opensearch_url])
