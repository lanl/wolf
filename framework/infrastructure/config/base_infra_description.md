# INFRASTRUCTURE DESCRIPTION:
The Workflow Orchestration Language Framework (WOLF) is an agentic AI framework that orchestrates users and AI agents, as they perform tasks.
WOLF provides an extensive modular and composable infrastructure to help AI agents perform tasks and workflows. 
The Infrastructure Layer consists of: 
provides modular, composable components for building Retrieval-Augmented Generation (RAG) systems and self-contained tool ecosystems.
Each component builds on the previous, creating a hierarchy of capabilities:

🧩 VStore [VS]: Vector store built on Chroma for embedding storage, retrieval, and async ingestion. [Defined in framework/data_stores/vstore.py]

🧠 Knowledgebase [KB]: Combines VStore with an SQLite inventory for metadata and traceable document management. [Defined in framework/knowledgebase/knowledge_base.py]

🔧 Tool: Describes, documents, and executes language-agnostic tools (Python, Bash, Go, etc.). [Defined in framework/tooling/tools.py]

🧰 ToolBox [TB]: Indexes and manages multiple tools, enabling discovery, execution, and documentation. [Defined in framework/tooling/toolbox.py]

📦 Universes [UN] (also called ActionBoxes [AB]): The environment layer that hosts KBs and TBs, providing APIs, discovery, and remote execution. [Defined in framework/universes/base_universe.py]


### 1. Importance of Locality
- Each Agent exists inside a local self-contained sandbox, and interacts with the infrastructure, users, or other agents through an interface called the "system". 
- The 'local' sandbox functions similarly to a universes, and can provide KBs, TBs, and a set of managed universes (which can contain their own KBs and TBs)
- It is consequently important to be aware of the locally of each component of the infrastruce when trying to interact with it, such as to avoid trying to call a tool or retrived a document from the wrong universe. 

### 2. infrastructure management awareness:
- Whenever necessary, you are ALLOWED and EXPECTED to configure, manage, or deploy parts of the infrastructure to perform your taks. I.e Creation of new tools to help with repetitive tasks, or deploy a new universe to solve software environment conflicts or to satisfy computer architecture requirement (run solftware stack on remote computers/machine/robot... that have the appropriate hardware i.e GPU, actuators, sensors, laser...)
