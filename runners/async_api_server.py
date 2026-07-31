import uvicorn
import asyncio
from framework.workflows.custom_workflows.async_api_workflow import app

if __name__ == "__main__":
    print("[+] Starting Cerberus Async API Server...")
    # Run the FastAPI app using uvicorn
    # host='0.0.0.0' makes it accessible on the local network
    # port=8000 is the default port
    uvicorn.run(app, host="0.0.0.0", port=8000)
