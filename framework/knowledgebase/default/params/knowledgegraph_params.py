# Mem0 config — matches test_mem0.py structure
Ollama_conf = {"provider": "ollama",
               "config": {"model": "gpt-oss:20b-128k",
                          "ollama_base_url":"http://10.0.10.160:11410",
                          "api_key":"sk-ollama",
                          "temperature": 0.1,
                          "max_tokens": 2000,
                          }
               }
Ollama_Embedder_conf = {"provider": "ollama",
                        "config": {"model": "nomic-embed-text:latest",
                                   "ollama_base_url":"http://10.0.10.160:11410",
                                   "api_key":"sk-ollama",
                                   "embedding_dims": 512
                                   }
                        }
LLM_conf = {"provider": "openai",
            "config": {"model": "ollama/gpt-oss:20b",
                       "openai_base_url":"http://10.0.10.160:4444",
                       "api_key":"sk-XXXX",
                       "temperature": 0.1,
                       "max_tokens": 2000,
                       }
            }
Embedder_conf = {"provider": "openai",
                 "config": {"model": "ollama/nomic-embed-text:latest",
                            "openai_base_url":"http://10.0.10.160:4444",
                            "api_key":"sk-XXXX",
                            "embedding_dims": 512
                            }
                 }

VStore_conf = {"provider": "chroma",
               "config": {"collection_name": "test",
                          "path": "db",
                          # Optional: ChromaDB Cloud configuration
                          # "api_key": "your-chroma-cloud-api-key",
                          # "tenant": "your-chroma-cloud-tenant-id",
                          }
               }

Graph_conf =   {"provider": "memgraph",
                "config": {"url": "bolt://10.0.10.160:7687",
                           "username": "memgraph",
                           "password": "your-password",
                           },
                }

openai_config = {"llm": LLM_conf,
                 "embedder": Embedder_conf,
                 "graph_store": Graph_conf,
                 "vector_store": VStore_conf,
                 }
ollama_config = {"llm": Ollama_conf,
                 "embedder": Ollama_Embedder_conf,
                 "graph_store": Graph_conf,
                 "vector_store": VStore_conf,
                 }

DEFAULT_OLLAMA_KNOWLEDGRAPH_PARAMS={
        "llm":Ollama_conf,
        "embedder":Ollama_Embedder_conf,
        "graph_store":Graph_conf,
        "vector_store":VStore_conf
        }

DEFAULT_OPENAI_KNOWLEDGRAPH_PARAMS={
        "llm":LLM_conf,
        "embedder":Embedder_conf,
        "graph_store":Graph_conf,
        "vector_store":VStore_conf
        }
