# wolf



## Getting started

# Welcome to the Workflow Orchestration Language Framework (WOLF)!

Follow these steps to use WOLF

# 1. Clone the WOLF repo
```sh
  ssh://git@re-git.lanl.gov:10022/mada/wolf.git
```

# 2. Set up the environment
## 2.1 Set up your conda env:
Setup the correct conda environement.

```sh
conda create -n wolf python=3.13  # Python version >=3.13
conda activate wolf
bash install_env.sh
pip install dotenv searxng_wrapper rich openai funkybob tiktoken pdfplumber nbformat alive_progress prompt_toolkit
pip install chromadb
pip install fastapi
```

## 2.2 Set up your .env

A sample of .env file, sample.env,is provided to help you get started: 
### 2.2.1 Make a copy
```sh
cp sample.env .env
```
### 2.2.1 Insert you inference API Key
Obtain an API key from eithet  [LANL AI Portal](https://aiportal-api.aws.lanl.gov/ui/) or Venadao,
and place the in the corresponding variable inside the .env file.

# 3. Run WOLF interactively

## 3.1 CLI interactive:
```sh
./wolf
```

## License
Modified BSD 3-Clause License

Copyright (c) 2025, Los Alamos National Laboratory

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

4. Redistributions or derivative works must give appropriate credit to the 
   original authors, including citation of the original publication or 
   repository.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
