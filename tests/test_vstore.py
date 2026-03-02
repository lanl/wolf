import asyncio
from framework.data_store.vstore import VectorStore
import pytest


async def test_vstore_recursive_upload(vs, docs_path, extensions=['md', 'html', 'py', 'js', 'ts']):
    await vs.recursive_upload(docs_path, extensions)
    print(f"Test vstore::recursive_upload PASSED")

async def test_vstore_query(vs):
    results = await vs.query('find all functions handling file I/O')
    for r in results: print(r['document'][:200] + "...")
    print(f"Test vstore::query PASSED")

async def test_vstore_purge(vs):
    await vs.purge()
    print(f"Test vstore::purge PASSED")

async def test_vstore_close(vs):
    await vs.close()
    print(f"Test vstore::close PASSED")




async def main():
    params = {
        'embedding_model': 'all-MiniLM-L6-v2',
        'chunk_size': 512,
        'chunk_overlap': 64,
        'collection_name': 'code_docs',
        'persist_directory': './chroma_db'
    }
    store = VectorStore(params)
    docs_exts=['md', 'html', 'py', 'js', 'ts']
    docs_dir = '../llama.cpp/docs'
    await test_vstore_recursive_upload(store, docs_dir, docs_exts)
    #await store.recursive_upload('../llama.cpp/docs', extensions=['md', 'html', 'py', 'js', 'ts'])
    await test_vstore_query(store)
    #results = await store.query('find all functions handling file I/O')
    #for r in results:
    #    print(r['document'][:200] + "...")

    await test_vstore_purge(store) #store.purge()
    await test_vstore_close(store) #store.close()

if __name__ == "__main__":
    asyncio.run(main())
    #pytest.main([__file__])

