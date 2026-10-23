"""Defense in depth for generated scene code, not a Python security sandbox.

Reject common system/file escape paths before approval and again in the host.
Cinema's native API still runs with the user's privileges.
"""
import ast

ALLOWED_IMPORTS = {'c4d', 'math', 'random', 'collections', 'itertools', 'functools', 'statistics'}
FORBIDDEN_NAMES = {
    'open', 'exec', 'eval', 'compile', '__import__', 'globals', 'locals', 'vars',
    'getattr', 'setattr', 'delattr', 'breakpoint', 'input', 'exit', 'quit',
    'help', 'memoryview',
}
FORBIDDEN_ATTRIBUTES = {
    'storage', 'plugins', 'threading', 'gui', 'modules', 'builtins',
    'CallCommand', 'CallButton', 'GeExecute', 'RenderDocument', 'SaveDocument',
    'LoadDocument', 'LoadFile', 'SaveFile', 'KillDocument', 'KillAllDocuments',
    'InsertBaseDocument', 'SetActiveDocument', 'MergeDocument',
    'GetMainThread', 'StopAllThreads', 'StartUndo', 'EndUndo',
    'Main', 'Write', 'Save',
    'output', 'output_path', 'render_request', 'files', 'BaseFile',
    'read_text', 'read_bytes', 'write_text', 'write_bytes', 'unlink', 'mkdir',
}


class UnsafeCode(ValueError):
    pass


def validate_code(code):
    tree = ast.parse(code, filename='<LLMLINK preview>', mode='exec')
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or '']
            if (isinstance(node, ast.ImportFrom) and node.level) or any(
                    name.split('.')[0] not in ALLOWED_IMPORTS or any(
                        part in FORBIDDEN_ATTRIBUTES or part.startswith('_') for part in name.split('.')) for name in modules):
                raise UnsafeCode('This import is not allowed in generated scene code. Use Cinema scene APIs and llmlink helpers.')
            if isinstance(node, ast.ImportFrom) and any(alias.name == '*' or alias.name in FORBIDDEN_ATTRIBUTES or alias.name.startswith('_') for alias in node.names):
                raise UnsafeCode('This import is not allowed in generated scene code.')
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith('__')):
            raise UnsafeCode('Unsafe Python name: '+node.id)
        if isinstance(node, ast.Attribute) and (node.attr.startswith('_') or node.attr in FORBIDDEN_ATTRIBUTES):
            raise UnsafeCode('Unsupported Python access: '+node.attr+'. Use llmlink.render/save_scene for output.')
    return compile(tree, '<LLMLINK Cinema4D>', 'exec')
