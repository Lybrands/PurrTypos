"""Tool handler modules.

每个子模块用 ``services.tool_runtime`` 的 ``@tool`` 装饰器注册 handler，
并由 ``services.tool_executor`` 在末尾 import 触发注册。
"""
