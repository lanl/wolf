import copy
from framework.data_store.data_models import EmbeddingParams, VectorStoreParams
Default_vStore_params = {
        "embedding_model": "all-MiniLM-L6-v2",
        "chunk_size": 512,
        "chunk_overlap": 64,
        "collection_name": "db",
        "persist_directory": "./vstore"
    }

#class VectorStoreParams(BaseModel):
#    """
#    This model is used for data validation and schema generation
#    related to vector stores
#    """
#    db_name: str = Field(..., description='Name of the db file inside whitch the vectore is stored')
#    persist_directory: str = Field(..., description='Path/where/to/store/the/db/file')
#    collection_name: str = Field(..., description="Name of initial data collection")
#    chunk_size: int = Field(256, description='Number of tokens per chunk')
#    chunk_overlap: int = Field(16, description='Number of tokens over which consecutive chinks overlap')
#    embedding: EmbeddingParams = Field(default=ALL_MINILM_L6_V2_Embedding_Params,
#                                             description=f""" Parameters of the embedding to use:
#                                             {EmbeddingParams.model_fields}""")
#    rebuild_vstore: bool = Field(False, description='Flag for rebuilding the vector store by purging the db file and reuploading the files')
#    vs_VRBZ: int = Field(0, description='Level of verbosity when operating on the vector store')
#    class Config:
#        # Pydantic V1: use schema_extra. Pydantic V2: use json_schema_extra
#        # This adds an extra top-level description to the generated schema
#        json_schema_extra = {
#            "description": "Main models are the models we use the most"
#        }


# Summaries
#Default_summaries_vs_params = copy.deepcopy(Default_vStore_params)
#Default_summaries_vs_params["collection_name"] = "summaries"
Default_summaries_vs_params = VectorStoreParams(db_name="summaries", 
                                                persist_directory="./",
                                                collection_name="summaries",
                                                chunk_size=512,
                                                chunk_overlap=64,
                                                embedding=EmbeddingParams(),
                                                rebuild_vstore=True,
                                                vs_VRBZ=0) 
# Traces
#Default_traces_vs_params = copy.deepcopy(Default_vStore_params)
#Default_traces_vs_params["collection_name"] = "traces"

Default_traces_vs_params = VectorStoreParams(db_name="traces",
                                                persist_directory="./",
                                                collection_name="traces",
                                                chunk_size=512,
                                                chunk_overlap=64,
                                                embedding=EmbeddingParams(),
                                                rebuild_vstore=True,
                                                vs_VRBZ=0)

