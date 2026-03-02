embedding_model = "all-MiniLM-L6-v2"
chunk_size      = 512
chunk_overlap   = 64
collection_name = "llamaCPP"
path_docs = "../llama.cpp/docs"
persist_directory = "./llamaCPP_db"
extensions=['md', 'html', 'py', 'js', 'ts']

import asyncio
from framework.tooling.tool_models import ToolMeta, FuncArg
from framework.tooling.tools import BaseTool
from framework.data_store.vstore import VectorStore

"""
func = ToolMeta(name="add", description="Add two numbers", args=[FuncArg(arg_name="a", arg_type="int", description="First number"), FuncArg(arg_name="b", arg_type="int", description="Second number")], body="return a + b", return_type=["int"], tool_type="python_func")
"""

docs_params = {
    "embedding_model": embedding_model,
    "chunk_size": chunk_size,
    "chunk_overlap": chunk_overlap,
    "collection_name": collection_name,
    "persist_directory": persist_directory,
    }


#tool_params = {"meta data": func, "docs params": docs_params}

#tool = BaseTool(tool_params)



async def demo():
    meta = ToolMeta(
        name="file_reader",
        description="Reads a file and returns its contents.",
        args=[FuncArg(arg_name="path", arg_type="str", description="Path to the file")],
        return_type=["str"],
        tool_type="python_func",
        purpose="Quick file reading utility",
    )

    tool = BaseTool({
        "meta": meta,
        #"docs_params": {
        #    "embedding_model": "all-MiniLM-L6-v2",
        #    "collection_name": "file_reader_docs",
        #    "persist_directory": "./tool_db/file_reader"
        #}
        "docs_params": docs_params
    })

    print(tool.describe())

    # Add docs from ./src
    #await tool.add_docs("./src", extensions=["py"])


    # Query docs
    results = await tool.query("open a file", n_results=3)
    for r in results:
        print(f"\nMatch in {r['source']}:{r['line_start']}-{r['line_end']}")
        print("Context:", r['context'][:200], "...\n")

    await tool.close()


if __name__ == "__main__":
    asyncio.run(demo())

