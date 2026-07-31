from framework.tooling.tool_models import ToolMeta, FuncArg
import pytest

def test_tool_models():
    func = ToolMeta(name="add", 
                    description="Add two numbers", 
                    args=[FuncArg(arg_name="a", 
                                  arg_type="int", 
                                  description="First number"), 
                          FuncArg(arg_name="b", 
                                  arg_type="int", 
                                  description="Second number")
                          ], 
                    body="return a + b", 
                    return_type=["int"], 
                    tool_type="python_func")
    assert func.name == "add"
    print(f"Test tool_models PASSED")

if __name__ == "__main__":
    # Run this file’s tests with pytest if executed directly
    test_tool_models()
    pytest.main([__file__])
