import difflib
import fnmatch
import json
import posixpath
import re
import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field

from .tools.ast_tool import parse_ast
from .tools.linter_tool import run_linter

# We import the path and exclusion helpers from project_chat
# Using deferred/lazy imports inside functions if necessary to avoid circular import issues


class ProposalChange(BaseModel):
    path: str
    operation: Literal["create", "replace", "delete"]
    content: str = ""


class Proposal(BaseModel):
    id: str
    summary: str
    diff: str
    changes: List[ProposalChange] = Field(default_factory=list)


class ProjectChatTraceItem(BaseModel):
    tool: str
    status: Literal["success", "error"]
    detail: str
    duration_ms: float


PROJECT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List file paths in the project snapshot, optionally filtered by a glob pattern (e.g. '*.py' or 'src/**/*.ts').",
            "parameters": {
                "type": "object",
                "properties": {
                    "glob": {
                        "type": "string",
                        "description": "Optional glob pattern to filter paths.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the text content of a file from the project snapshot, with optional 1-based start and end line bounds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative POSIX path of the file to read from the project snapshot.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "1-based starting line number (inclusive). Defaults to 1.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-based ending line number (inclusive). Defaults to end of file.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Perform a literal text search across files in the project snapshot, returning matching lines and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Literal text string to search for.",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Optional glob pattern to restrict which files are searched.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return (capped at 50).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_project",
            "description": "Inspect project manifests (package.json, pyproject.toml, requirements.txt, Cargo.toml, go.mod, *.csproj, etc.) to detect declared capabilities, scripts, and framework signals.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_changes",
            "description": "Propose modifications to project files by generating a unified diff. Changes are staged in a proposal and NOT applied to disk or the snapshot. Supports create, replace, and delete operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "changes": {
                        "type": "array",
                        "description": "List of changes to propose (max 20 changes).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Relative POSIX path of the file to create, replace, or delete.",
                                },
                                "operation": {
                                    "type": "string",
                                    "enum": ["create", "replace", "delete"],
                                    "description": "Type of change: 'create' for new files, 'replace' for existing files, 'delete' to remove existing files.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "New content for the file (required for 'create' and 'replace'; empty for 'delete'). Max 30,000 characters per file.",
                                },
                            },
                            "required": ["path", "operation"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["changes"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_python",
            "description": "Perform static analysis on a Python file in the project snapshot using AST parsing and pyflakes linting without code execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative POSIX path of the Python file to analyze.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_json",
            "description": "Validate a JSON file in the project snapshot using strict standard library parsing, reporting exact line/column on syntax errors, rejecting non-standard NaN/Infinity, and detecting duplicate keys.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative POSIX path of the JSON file to validate.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_conflicts",
            "description": "Scan project snapshot files for anchored Git merge conflict markers (<<<<<<<, =======, >>>>>>>, |||||||), returning paths and line numbers (up to 100 results).",
            "parameters": {
                "type": "object",
                "properties": {
                    "glob": {
                        "type": "string",
                        "description": "Optional glob pattern to restrict which files are scanned.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
]


def _get_path_validators():
    from .project_chat import is_excluded_file_path, is_safe_relative_posix_path
    return is_safe_relative_posix_path, is_excluded_file_path


def handle_list_files(
    snapshot: Dict[str, str],
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Literal["success", "error"], str]:
    glob_pattern = args.get("glob")
    if glob_pattern is not None:
        if not isinstance(glob_pattern, str):
            return (
                {"error": "Argument 'glob' must be a string if provided."},
                "error",
                "Invalid glob argument",
            )
        glob_pattern = glob_pattern.strip()

    all_paths = sorted(snapshot.keys())
    if glob_pattern:
        try:
            matched = [p for p in all_paths if fnmatch.fnmatch(p, glob_pattern)]
        except Exception as exc:
            return (
                {"error": f"Invalid glob pattern '{glob_pattern}': {exc}"},
                "error",
                f"Invalid glob pattern: {exc}",
            )
        detail = f"Listed {len(matched)} file(s) matching '{glob_pattern}'"
        return (
            {"files": matched, "count": len(matched), "total_in_snapshot": len(all_paths), "glob": glob_pattern},
            "success",
            detail,
        )

    detail = f"Listed all {len(all_paths)} file(s) in snapshot"
    return (
        {"files": all_paths, "count": len(all_paths), "total_in_snapshot": len(all_paths)},
        "success",
        detail,
    )


def handle_read_file(
    snapshot: Dict[str, str],
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Literal["success", "error"], str]:
    is_safe, is_excluded = _get_path_validators()

    path = args.get("path")
    if not path or not isinstance(path, str):
        return (
            {"error": "Missing or invalid 'path' parameter. Must be a non-empty string."},
            "error",
            "Missing path parameter",
        )

    if not is_safe(path):
        return (
            {"error": f"Invalid or unsafe file path: '{path}'. Must be a safe relative POSIX path."},
            "error",
            f"Unsafe path '{path}' rejected",
        )

    if is_excluded(path):
        return (
            {"error": f"Path '{path}' is excluded (secret, binary, or private path)."},
            "error",
            f"Excluded path '{path}' rejected",
        )

    norm = posixpath.normpath(path)
    if norm not in snapshot:
        return (
            {"error": f"File '{norm}' not found in project snapshot."},
            "error",
            f"File '{norm}' not found in snapshot",
        )

    content = snapshot[norm]
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    start_line_raw = args.get("start_line")
    end_line_raw = args.get("end_line")

    start_line = 1
    end_line = total_lines if total_lines > 0 else 1

    if start_line_raw is not None:
        try:
            start_line = int(start_line_raw)
        except (ValueError, TypeError):
            return (
                {"error": f"Argument 'start_line' must be an integer, got '{start_line_raw}'."},
                "error",
                "Invalid start_line argument",
            )

    if end_line_raw is not None:
        try:
            end_line = int(end_line_raw)
        except (ValueError, TypeError):
            return (
                {"error": f"Argument 'end_line' must be an integer, got '{end_line_raw}'."},
                "error",
                "Invalid end_line argument",
            )

    if total_lines == 0:
        return (
            {
                "path": norm,
                "start_line": 1,
                "end_line": 1,
                "total_lines": 0,
                "content": "",
            },
            "success",
            f"Read 0 lines from empty file '{norm}'",
        )

    if start_line < 1:
        start_line = 1
    if end_line > total_lines:
        end_line = total_lines

    if start_line > total_lines:
        return (
            {"error": f"start_line ({start_line}) exceeds total lines ({total_lines})."},
            "error",
            f"start_line {start_line} out of bounds for '{norm}'",
        )

    if start_line > end_line:
        return (
            {"error": f"start_line ({start_line}) cannot be greater than end_line ({end_line})."},
            "error",
            f"Invalid line range {start_line}-{end_line} for '{norm}'",
        )

    selected = lines[start_line - 1 : end_line]
    sliced_text = "".join(selected)

    # Output bounding
    truncated = False
    if len(sliced_text) > 30000:
        sliced_text = sliced_text[:30000]
        truncated = True

    result_data: Dict[str, Any] = {
        "path": norm,
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": total_lines,
        "content": sliced_text,
    }
    if truncated:
        result_data["truncated"] = True

    return (
        result_data,
        "success",
        f"Read lines {start_line}-{end_line} of {total_lines} from '{norm}'",
    )


def handle_search_files(
    snapshot: Dict[str, str],
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Literal["success", "error"], str]:
    query = args.get("query")
    if not query or not isinstance(query, str):
        return (
            {"error": "Missing or empty 'query' parameter for literal text search."},
            "error",
            "Missing query parameter",
        )

    glob_pattern = args.get("glob")
    if glob_pattern is not None:
        if not isinstance(glob_pattern, str):
            return (
                {"error": "Argument 'glob' must be a string if provided."},
                "error",
                "Invalid glob argument",
            )
        glob_pattern = glob_pattern.strip()

    max_results_raw = args.get("max_results")
    max_results = 20
    if max_results_raw is not None:
        try:
            max_results = int(max_results_raw)
            if max_results <= 0:
                max_results = 20
        except (ValueError, TypeError):
            max_results = 20
    max_results = min(max_results, 50)

    target_paths = sorted(snapshot.keys())
    if glob_pattern:
        try:
            target_paths = [p for p in target_paths if fnmatch.fnmatch(p, glob_pattern)]
        except Exception as exc:
            return (
                {"error": f"Invalid glob pattern '{glob_pattern}': {exc}"},
                "error",
                f"Invalid glob pattern: {exc}",
            )

    matches: List[Dict[str, Any]] = []
    for p in target_paths:
        content = snapshot[p]
        lines = content.splitlines(keepends=True)
        for idx, line in enumerate(lines, start=1):
            if query in line:
                matches.append({
                    "path": p,
                    "line": idx,
                    "line_content": line[:200],
                })
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    detail = f"Found {len(matches)} match(es) for literal '{query[:30]}'"
    return (
        {
            "query": query,
            "matches": matches,
            "count": len(matches),
            "capped": len(matches) >= max_results,
        },
        "success",
        detail,
    )


def _inspect_package_json(content: str) -> Dict[str, Any]:
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            return {"error": "package.json root is not an object"}
        return {
            "name": data.get("name", ""),
            "version": data.get("version", ""),
            "scripts": data.get("scripts", {}) if isinstance(data.get("scripts"), dict) else {},
            "dependencies": sorted(list(data.get("dependencies", {}).keys())) if isinstance(data.get("dependencies"), dict) else [],
            "devDependencies": sorted(list(data.get("devDependencies", {}).keys())) if isinstance(data.get("devDependencies"), dict) else [],
        }
    except Exception as exc:
        return {"error": f"Failed to parse package.json: {exc}"}


def _inspect_requirements_txt(content: str) -> Dict[str, Any]:
    deps: List[str] = []
    for line in content.splitlines(keepends=True):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([a-zA-Z0-9_.-]+)", line)
        if m:
            deps.append(m.group(1).lower())
    return {"dependencies": sorted(list(set(deps)))}


def _inspect_pyproject_toml(content: str) -> Dict[str, Any]:
    deps: List[str] = []
    in_dependencies = False
    for line in content.splitlines(keepends=True):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^\[.*dependencies.*\]", line, re.IGNORECASE):
            in_dependencies = True
            continue
        elif line.startswith("["):
            in_dependencies = False

        if in_dependencies:
            m = re.match(r'^"?([a-zA-Z0-9_.-]+)"?\s*=', line)
            if m:
                deps.append(m.group(1).lower())
        else:
            # Also catch dependencies array items: "fastapi>=0.100.0",
            m = re.match(r'^"([a-zA-Z0-9_.-]+)[><=~^]', line)
            if m:
                deps.append(m.group(1).lower())
    return {"dependencies": sorted(list(set(deps)))}


def _inspect_cargo_toml(content: str) -> Dict[str, Any]:
    deps: List[str] = []
    in_deps = False
    name = ""
    for line in content.splitlines(keepends=True):
        line = line.strip()
        if line.startswith("[package]"):
            in_deps = False
        elif line.startswith("[dependencies]") or line.startswith("[dev-dependencies]"):
            in_deps = True
        elif line.startswith("["):
            in_deps = False
        elif line.startswith("name ="):
            m = re.search(r'name\s*=\s*"([^"]+)"', line)
            if m:
                name = m.group(1)
        elif in_deps:
            m = re.match(r'^([a-zA-Z0-9_-]+)\s*=', line)
            if m:
                deps.append(m.group(1))
    return {"name": name, "dependencies": sorted(list(set(deps)))}


def _inspect_go_mod(content: str) -> Dict[str, Any]:
    module = ""
    requires: List[str] = []
    for line in content.splitlines(keepends=True):
        line = line.strip()
        if line.startswith("module "):
            module = line.split("module ", 1)[1].strip()
        elif line.startswith("require "):
            parts = line.split()
            if len(parts) >= 2:
                requires.append(parts[1])
        elif re.match(r"^[a-zA-Z0-9_./-]+\s+v[0-9]", line):
            parts = line.split()
            requires.append(parts[0])
    return {"module": module, "dependencies": sorted(requires)}


def _inspect_csproj(content: str) -> Dict[str, Any]:
    target_framework = ""
    packages: List[str] = []
    m_tf = re.search(r"<TargetFramework>([^<]+)</TargetFramework>", content, re.IGNORECASE)
    if m_tf:
        target_framework = m_tf.group(1)
    for m_pkg in re.finditer(r'<PackageReference\s+Include="([^"]+)"', content, re.IGNORECASE):
        packages.append(m_pkg.group(1))
    return {"target_framework": target_framework, "dependencies": sorted(packages)}


def _inspect_gemfile(content: str) -> Dict[str, Any]:
    gems: List[str] = []
    for m in re.finditer(r"""^\s*gem\s+['"]([^'"]+)['"]""", content, re.MULTILINE):
        gems.append(m.group(1))
    return {"gems": sorted(gems)}


def _inspect_composer_json(content: str) -> Dict[str, Any]:
    try:
        data = json.loads(content)
        req = sorted(list(data.get("require", {}).keys())) if isinstance(data.get("require"), dict) else []
        req_dev = sorted(list(data.get("require-dev", {}).keys())) if isinstance(data.get("require-dev"), dict) else []
        scripts = data.get("scripts", {}) if isinstance(data.get("scripts"), dict) else {}
        return {"dependencies": req, "devDependencies": req_dev, "scripts": scripts}
    except Exception as exc:
        return {"error": f"Failed to parse composer.json: {exc}"}


def _inspect_pubspec_yaml(content: str) -> Dict[str, Any]:
    deps: List[str] = []
    in_deps = False
    for line in content.splitlines(keepends=True):
        if line.startswith("dependencies:"):
            in_deps = True
            continue
        elif line.startswith("dev_dependencies:") or (line and not line.startswith(" ") and not line.startswith("#")):
            in_deps = False
        if in_deps and line.startswith("  "):
            m = re.match(r"^\s+([a-zA-Z0-9_-]+):", line)
            if m:
                deps.append(m.group(1))
    return {"dependencies": sorted(deps)}


def _inspect_pom_xml(content: str) -> Dict[str, Any]:
    artifacts: List[str] = []
    for m in re.finditer(r"<artifactId>([^<]+)</artifactId>", content):
        artifacts.append(m.group(1))
    return {"artifacts": sorted(artifacts)}


def detect_frameworks(snapshot: Dict[str, str], manifests: Dict[str, Any]) -> List[str]:
    detected: Set[str] = set()
    all_paths = set(snapshot.keys())

    # Aggregate JS/TS deps & devDeps
    js_deps: Set[str] = set()
    for m_path, m_val in manifests.items():
        if m_path.endswith("package.json") and isinstance(m_val, dict):
            js_deps.update(d.lower() for d in m_val.get("dependencies", []))
            js_deps.update(d.lower() for d in m_val.get("devDependencies", []))

    # Aggregate Python deps
    py_deps: Set[str] = set()
    for m_path, m_val in manifests.items():
        if (m_path.endswith("requirements.txt") or m_path.endswith("pyproject.toml")) and isinstance(m_val, dict):
            py_deps.update(d.lower() for d in m_val.get("dependencies", []))

    # React
    if "react" in js_deps or "react-dom" in js_deps or any(p.endswith(".jsx") or p.endswith(".tsx") for p in all_paths):
        detected.add("React")

    # Vue
    if "vue" in js_deps or any(p.endswith(".vue") for p in all_paths):
        detected.add("Vue")

    # Svelte
    if "svelte" in js_deps or "@sveltejs/kit" in js_deps or any(p.endswith(".svelte") or p.startswith("svelte.config.") for p in all_paths):
        detected.add("Svelte")

    # Next
    if "next" in js_deps or any(p.startswith("next.config.") or "/next.config." in p for p in all_paths):
        detected.add("Next")

    # Nuxt
    if "nuxt" in js_deps or "nuxt3" in js_deps or any(p.startswith("nuxt.config.") or "/nuxt.config." in p for p in all_paths):
        detected.add("Nuxt")

    # Angular
    if "@angular/core" in js_deps or any(p == "angular.json" or p.endswith("/angular.json") for p in all_paths):
        detected.add("Angular")

    # Express
    if "express" in js_deps:
        detected.add("Express")

    # Nest
    if "@nestjs/core" in js_deps or "@nestjs/common" in js_deps or any(p == "nest-cli.json" or p.endswith("/nest-cli.json") for p in all_paths):
        detected.add("Nest")

    # FastAPI
    if "fastapi" in py_deps or any("from fastapi" in content or "import fastapi" in content for content in snapshot.values()):
        detected.add("FastAPI")

    # Django
    if "django" in py_deps or any(p == "manage.py" or p.endswith("/manage.py") for p in all_paths):
        detected.add("Django")

    # Flask
    if "flask" in py_deps or any("from flask" in content or "import flask" in content for content in snapshot.values()):
        detected.add("Flask")

    # Rails
    gemfile_data = next((v for k, v in manifests.items() if k.endswith("Gemfile")), None)
    if (gemfile_data and "rails" in gemfile_data.get("gems", [])) or any(p == "config/routes.rb" or p == "bin/rails" for p in all_paths):
        detected.add("Rails")

    # Laravel
    composer_data = next((v for k, v in manifests.items() if k.endswith("composer.json")), None)
    if (composer_data and "laravel/framework" in composer_data.get("dependencies", [])) or any(p == "artisan" for p in all_paths):
        detected.add("Laravel")

    # Spring
    pom_data = next((v for k, v in manifests.items() if k.endswith("pom.xml")), None)
    if (pom_data and any("spring" in a.lower() for a in pom_data.get("artifacts", []))) or any("spring-boot" in content or "springframework" in content for content in snapshot.values()):
        detected.add("Spring")

    # .NET
    if any(p.endswith(".csproj") or p.endswith(".fsproj") or p.endswith(".sln") for p in all_paths) or any("Microsoft.NET" in content or "TargetFramework" in content for content in snapshot.values()):
        detected.add(".NET")

    # Flutter
    pubspec_data = next((v for k, v in manifests.items() if k.endswith("pubspec.yaml")), None)
    if (pubspec_data and "flutter" in pubspec_data.get("dependencies", [])) or any(p == "pubspec.yaml" and "flutter:" in snapshot.get(p, "") for p in all_paths):
        detected.add("Flutter")

    # Electron
    if "electron" in js_deps:
        detected.add("Electron")

    # Tauri
    cargo_data = next((v for k, v in manifests.items() if k.endswith("Cargo.toml")), None)
    if (
        (cargo_data and "tauri" in cargo_data.get("dependencies", []))
        or "@tauri-apps/api" in js_deps
        or "@tauri-apps/cli" in js_deps
        or any(p == "tauri.conf.json" or "src-tauri" in p for p in all_paths)
    ):
        detected.add("Tauri")

    return sorted(list(detected))


def handle_inspect_project(
    snapshot: Dict[str, str],
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Literal["success", "error"], str]:
    manifests: Dict[str, Any] = {}
    build_scripts: Dict[str, Any] = {}

    for path, content in sorted(snapshot.items()):
        name = posixpath.basename(path).lower()
        if name == "package.json":
            res = _inspect_package_json(content)
            manifests[path] = res
            if "scripts" in res and isinstance(res["scripts"], dict):
                for s_name, s_cmd in res["scripts"].items():
                    build_scripts[f"{path}:{s_name}"] = s_cmd
        elif name == "requirements.txt":
            manifests[path] = _inspect_requirements_txt(content)
        elif name == "pyproject.toml":
            manifests[path] = _inspect_pyproject_toml(content)
        elif name == "cargo.toml":
            manifests[path] = _inspect_cargo_toml(content)
        elif name == "go.mod":
            manifests[path] = _inspect_go_mod(content)
        elif path.endswith(".csproj"):
            manifests[path] = _inspect_csproj(content)
        elif name == "gemfile":
            manifests[path] = _inspect_gemfile(content)
        elif name == "composer.json":
            res = _inspect_composer_json(content)
            manifests[path] = res
            if "scripts" in res and isinstance(res["scripts"], dict):
                for s_name, s_cmd in res["scripts"].items():
                    build_scripts[f"{path}:{s_name}"] = s_cmd
        elif name == "pubspec.yaml":
            manifests[path] = _inspect_pubspec_yaml(content)
        elif name == "pom.xml":
            manifests[path] = _inspect_pom_xml(content)

    frameworks = detect_frameworks(snapshot, manifests)

    # Extension distribution
    ext_counts: Dict[str, int] = {}
    for p in snapshot.keys():
        ext = posixpath.splitext(p)[1].lower() or "[no-extension]"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    detail = f"Inspected project: {len(frameworks)} framework(s) detected, {len(manifests)} manifest(s) found"
    return (
        {
            "manifests": manifests,
            "detected_frameworks": frameworks,
            "total_files": len(snapshot),
            "file_extensions": dict(sorted(ext_counts.items(), key=lambda item: (-item[1], item[0]))),
            "declared_capabilities": {
                "scripts": build_scripts,
                "note": "Capabilities are declared in snapshot manifests; not installed runtime environments or exhaustive.",
            },
        },
        "success",
        detail,
    )


def handle_propose_changes(
    snapshot: Dict[str, str],
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Literal["success", "error"], str, Optional[Dict[str, Any]]]:
    is_safe, is_excluded = _get_path_validators()

    changes = args.get("changes")
    if not isinstance(changes, list) or len(changes) == 0:
        return (
            {"error": "Argument 'changes' must be a non-empty list of change objects."},
            "error",
            "Missing or empty changes argument",
            None,
        )

    if len(changes) > 20:
        return (
            {"error": f"Exceeded maximum limit of 20 changes (received {len(changes)})."},
            "error",
            f"Exceeded 20 changes limit ({len(changes)})",
            None,
        )

    seen_paths: Set[str] = set()
    total_chars = 0
    sanitized_changes: List[Dict[str, str]] = []

    for idx, c in enumerate(changes):
        if not isinstance(c, dict):
            return (
                {"error": f"Change item at index {idx} must be a JSON object."},
                "error",
                f"Invalid change item at index {idx}",
                None,
            )

        path = c.get("path")
        if not path or not isinstance(path, str):
            return (
                {"error": f"Change item at index {idx} has missing or non-string 'path'."},
                "error",
                f"Missing path at index {idx}",
                None,
            )

        if not is_safe(path):
            return (
                {"error": f"Invalid or unsafe file path: '{path}'. Must be a safe relative POSIX path."},
                "error",
                f"Unsafe path '{path}' rejected",
                None,
            )

        if is_excluded(path):
            return (
                {"error": f"Path '{path}' is excluded (secret, binary, or private path)."},
                "error",
                f"Excluded path '{path}' rejected",
                None,
            )

        norm_path = posixpath.normpath(path)
        if norm_path in seen_paths:
            return (
                {"error": f"Duplicate path detected in proposal changes: '{norm_path}'."},
                "error",
                f"Duplicate path '{norm_path}' in proposal",
                None,
            )
        seen_paths.add(norm_path)

        op = c.get("operation")
        if op not in ("create", "replace", "delete"):
            return (
                {"error": f"Invalid operation '{op}' for path '{norm_path}'. Must be 'create', 'replace', or 'delete'."},
                "error",
                f"Invalid operation '{op}'",
                None,
            )

        content = c.get("content")
        if op == "delete":
            content = ""
        else:
            if not isinstance(content, str):
                return (
                    {"error": f"Content for '{norm_path}' must be a string."},
                    "error",
                    f"Non-string content for '{norm_path}'",
                    None,
                )
            if re.search(r"[\x00-\x08\x0e-\x1f\ufffd]", content):
                return (
                    {"error": f"Content for '{norm_path}' contains unsupported binary or control characters."},
                    "error",
                    f"Binary characters in '{norm_path}'",
                    None,
                )
            if len(content) > 30000:
                return (
                    {"error": f"Content for '{norm_path}' exceeds maximum allowed limit of 30,000 characters ({len(content)} chars)."},
                    "error",
                    f"File '{norm_path}' exceeds 30,000 char limit",
                    None,
                )

        total_chars += len(content)

        # Existence validation
        if op == "create":
            if norm_path in snapshot:
                return (
                    {"error": f"Cannot create '{norm_path}': file already exists in snapshot. Use 'replace' instead."},
                    "error",
                    f"Cannot create existing file '{norm_path}'",
                    None,
                )
        elif op == "replace":
            if norm_path not in snapshot:
                return (
                    {"error": f"Cannot replace '{norm_path}': file does not exist in snapshot. Use 'create' instead."},
                    "error",
                    f"Cannot replace non-existent file '{norm_path}'",
                    None,
                )
        elif op == "delete":
            if norm_path not in snapshot:
                return (
                    {"error": f"Cannot delete '{norm_path}': file does not exist in snapshot."},
                    "error",
                    f"Cannot delete non-existent file '{norm_path}'",
                    None,
                )

        sanitized_changes.append({
            "path": norm_path,
            "operation": op,
            "content": content,
        })

    if total_chars > 200000:
        return (
            {"error": f"Total proposed content size ({total_chars} chars) exceeds maximum allowed limit of 200,000 characters."},
            "error",
            "Total content exceeds 200,000 char budget",
            None,
        )

    # Generate unified diffs
    diff_parts: List[str] = []
    for sc in sanitized_changes:
        norm_path = sc["path"]
        op = sc["operation"]
        content = sc["content"]

        if op == "create":
            orig_lines: List[str] = []
            new_lines = content.splitlines(keepends=True)
            fromfile = "/dev/null"
            tofile = f"b/{norm_path}"
        elif op == "replace":
            orig_lines = snapshot[norm_path].splitlines(keepends=True)
            new_lines = content.splitlines(keepends=True)
            fromfile = f"a/{norm_path}"
            tofile = f"b/{norm_path}"
        else:  # delete
            orig_lines = snapshot[norm_path].splitlines(keepends=True)
            new_lines = []
            fromfile = f"a/{norm_path}"
            tofile = "/dev/null"

        diff_lines = list(difflib.unified_diff(orig_lines, new_lines, fromfile=fromfile, tofile=tofile))
        if diff_lines:
            diff_parts.append("".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in diff_lines
            ))

    full_diff = "".join(diff_parts)

    proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
    summary_ops = ", ".join(f"{c['operation']} {c['path']}" for c in sanitized_changes[:3])
    if len(sanitized_changes) > 3:
        summary_ops += f", and {len(sanitized_changes) - 3} more"
    summary = f"Proposed changes to {len(sanitized_changes)} file(s): {summary_ops}"

    proposal_obj: Dict[str, Any] = {
        "id": proposal_id,
        "summary": summary,
        "diff": full_diff,
        "changes": sanitized_changes,
    }

    output_message = {
        "status": "proposal_generated",
        "proposal_id": proposal_id,
        "summary": summary,
        "diff": full_diff,
        "changes_count": len(sanitized_changes),
        "applied": False,
        "note": "Proposal generated successfully. Changes are staged for review and NOT applied to disk or the snapshot.",
    }

    detail = f"Generated proposal {proposal_id} ({len(sanitized_changes)} change(s))"
    return (output_message, "success", detail, proposal_obj)


ANCHORED_CONFLICT_PATTERN = re.compile(r"^(<{7}|={7}|>{7}|\|{7})(?: .*|\t.*)?$")


def _reject_json_constant(c: str) -> None:
    raise ValueError(f"Non-standard JSON constant '{c}' is not allowed (strict standard JSON requires numeric literals).")


def _bound_output(out: Dict[str, Any], max_chars: int = 15000) -> Dict[str, Any]:
    serialized = json.dumps(out)
    if len(serialized) <= max_chars:
        return out

    bounded = dict(out)
    bounded["truncated"] = True

    list_keys = [k for k, v in bounded.items() if isinstance(v, list)]
    nested_list_keys = []
    for k, v in bounded.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, list):
                    nested_list_keys.append((k, sub_k))

    for _ in range(10):
        for k in list_keys:
            if isinstance(bounded[k], list) and len(bounded[k]) > 5:
                bounded[k] = bounded[k][: max(5, len(bounded[k]) // 2)]
        for k, sub_k in nested_list_keys:
            if isinstance(bounded.get(k), dict) and isinstance(bounded[k].get(sub_k), list) and len(bounded[k][sub_k]) > 5:
                bounded[k] = dict(bounded[k])
                bounded[k][sub_k] = bounded[k][sub_k][: max(5, len(bounded[k][sub_k]) // 2)]
        serialized = json.dumps(bounded)
        if len(serialized) <= max_chars:
            return bounded

    for k in list_keys:
        if isinstance(bounded[k], list):
            bounded[k] = bounded[k][:3]
    for k, sub_k in nested_list_keys:
        if isinstance(bounded.get(k), dict) and isinstance(bounded[k].get(sub_k), list):
            bounded[k] = dict(bounded[k])
            bounded[k][sub_k] = bounded[k][sub_k][:3]

    serialized = json.dumps(bounded)
    if len(serialized) <= max_chars:
        return bounded

    return {
        "truncated": True,
        "note": f"Output exceeded maximum limit of {max_chars} characters. Read a smaller range for details.",
    }


def handle_analyze_python(
    snapshot: Dict[str, str],
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Literal["success", "error"], str]:
    is_safe, is_excluded = _get_path_validators()

    if not isinstance(args, dict):
        return (
            {"error": "Invalid arguments. Expected an object with a 'path' parameter."},
            "error",
            "Invalid arguments",
        )

    path = args.get("path")
    if not path or not isinstance(path, str):
        return (
            {"error": "Missing or invalid 'path' parameter. Must be a non-empty string."},
            "error",
            "Missing path parameter",
        )

    if not is_safe(path):
        return (
            {"error": f"Invalid or unsafe file path: '{path}'. Must be a safe relative POSIX path."},
            "error",
            f"Unsafe path '{path}' rejected",
        )

    if is_excluded(path):
        return (
            {"error": f"Path '{path}' is excluded (secret, binary, or private path)."},
            "error",
            f"Excluded path '{path}' rejected",
        )

    norm = posixpath.normpath(path)
    if norm not in snapshot:
        return (
            {"error": f"File '{norm}' not found in project snapshot."},
            "error",
            f"File '{norm}' not found in snapshot",
        )

    content = snapshot[norm]
    if len(content) > 30000:
        return (
            {"error": f"File '{norm}' exceeds maximum allowed limit of 30,000 characters ({len(content)} chars)."},
            "error",
            f"File '{norm}' exceeds 30k limit",
        )

    ast_res = parse_ast(content)
    linter_res = run_linter(content)

    valid_syntax = bool(ast_res.get("valid_syntax", False))
    out: Dict[str, Any] = {
        "path": norm,
        "valid_syntax": valid_syntax,
        "ast": ast_res,
        "linter": linter_res,
        "total_linter_issues": linter_res.get("total_issues", 0),
    }

    if not valid_syntax:
        out["syntax_error"] = {
            "error": ast_res.get("error", "SyntaxError"),
            "line": ast_res.get("line"),
            "column": ast_res.get("column"),
            "text": ast_res.get("text", ""),
        }
        detail = f"Analyzed '{norm}': syntax error at line {ast_res.get('line')}: {ast_res.get('error')}"
    else:
        fn_count = len(ast_res.get("functions", []))
        cls_count = len(ast_res.get("classes", []))
        issues_count = linter_res.get("total_issues", 0)
        detail = f"Analyzed '{norm}': valid syntax, {fn_count} function(s), {cls_count} class(es), {issues_count} linter issue(s)"

    out = _bound_output(out, max_chars=15000)
    return (out, "success", detail)


def handle_validate_json(
    snapshot: Dict[str, str],
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Literal["success", "error"], str]:
    is_safe, is_excluded = _get_path_validators()

    if not isinstance(args, dict):
        return (
            {"error": "Invalid arguments. Expected an object with a 'path' parameter."},
            "error",
            "Invalid arguments",
        )

    path = args.get("path")
    if not path or not isinstance(path, str):
        return (
            {"error": "Missing or invalid 'path' parameter. Must be a non-empty string."},
            "error",
            "Missing path parameter",
        )

    if not is_safe(path):
        return (
            {"error": f"Invalid or unsafe file path: '{path}'. Must be a safe relative POSIX path."},
            "error",
            f"Unsafe path '{path}' rejected",
        )

    if is_excluded(path):
        return (
            {"error": f"Path '{path}' is excluded (secret, binary, or private path)."},
            "error",
            f"Excluded path '{path}' rejected",
        )

    norm = posixpath.normpath(path)
    if norm not in snapshot:
        return (
            {"error": f"File '{norm}' not found in project snapshot."},
            "error",
            f"File '{norm}' not found in snapshot",
        )

    content = snapshot[norm]
    if len(content) > 30000:
        return (
            {"error": f"File '{norm}' exceeds maximum allowed limit of 30,000 characters ({len(content)} chars)."},
            "error",
            f"File '{norm}' exceeds 30k limit",
        )

    duplicate_keys: List[str] = []

    def _pairs_hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        seen: Set[str] = set()
        for k, v in pairs:
            if k in seen:
                duplicate_keys.append(k)
            seen.add(k)
        return dict(pairs)

    try:
        data = json.loads(content, parse_constant=_reject_json_constant, object_pairs_hook=_pairs_hook)
        parsed_type = type(data).__name__
        unique_dups = sorted(list(set(duplicate_keys)))
        warnings: List[str] = [f"Duplicate key '{k}' detected in JSON object." for k in unique_dups]

        out: Dict[str, Any] = {
            "path": norm,
            "valid": True,
            "type": parsed_type,
            "warnings": warnings,
            "duplicate_keys": unique_dups,
            "has_duplicate_keys": len(unique_dups) > 0,
        }
        if isinstance(data, dict):
            out["keys_count"] = len(data)
        elif isinstance(data, list):
            out["item_count"] = len(data)

        if unique_dups:
            out["duplicate_key_warning"] = f"Document contains duplicate key(s): {', '.join(unique_dups)}"
            detail = f"Validated '{norm}': valid JSON with {len(unique_dups)} duplicate key warning(s)"
        else:
            detail = f"Validated '{norm}': valid JSON ({parsed_type})"

        out = _bound_output(out, max_chars=15000)
        return (out, "success", detail)

    except json.JSONDecodeError as err:
        out = {
            "path": norm,
            "valid": False,
            "error": f"JSON decode error: {err.msg}",
            "message": err.msg,
            "line": err.lineno,
            "column": err.colno,
        }
        detail = f"Invalid JSON in '{norm}' at line {err.lineno}, column {err.colno}: {err.msg}"
        out = _bound_output(out, max_chars=15000)
        return (out, "success", detail)

    except ValueError as err:
        err_str = str(err)
        line_no, col_no = 1, 1
        for token in ("NaN", "Infinity", "-Infinity"):
            if token in content:
                idx = content.find(token)
                lines_before = content[:idx].splitlines(keepends=True)
                line_no = len(lines_before) if lines_before else 1
                col_no = len(lines_before[-1]) + 1 if lines_before else idx + 1
                break

        out = {
            "path": norm,
            "valid": False,
            "error": err_str,
            "message": err_str,
            "line": line_no,
            "column": col_no,
        }
        detail = f"Invalid JSON in '{norm}' at line {line_no}, column {col_no}: {err_str}"
        out = _bound_output(out, max_chars=15000)
        return (out, "success", detail)


def handle_check_conflicts(
    snapshot: Dict[str, str],
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Literal["success", "error"], str]:
    is_safe, is_excluded = _get_path_validators()

    if not isinstance(args, dict):
        args = {}

    glob_pattern = args.get("glob")
    if glob_pattern is not None:
        if not isinstance(glob_pattern, str):
            return (
                {"error": "Argument 'glob' must be a string if provided."},
                "error",
                "Invalid glob argument",
            )
        glob_pattern = glob_pattern.strip()

    target_paths = [p for p in sorted(snapshot.keys()) if is_safe(p) and not is_excluded(p)]
    if glob_pattern:
        try:
            target_paths = [p for p in target_paths if fnmatch.fnmatch(p, glob_pattern)]
        except Exception as exc:
            return (
                {"error": f"Invalid glob pattern '{glob_pattern}': {exc}"},
                "error",
                f"Invalid glob pattern: {exc}",
            )

    matches: List[Dict[str, Any]] = []
    capped = False

    for p in target_paths:
        content = snapshot[p]
        lines = content.splitlines(keepends=True)
        for idx, line in enumerate(lines, start=1):
            stripped_line = line.rstrip("\r\n")
            m = ANCHORED_CONFLICT_PATTERN.match(stripped_line)
            if m:
                marker_type = m.group(1)
                item = {
                    "path": p,
                    "line": idx,
                    "marker": marker_type,
                    "line_content": stripped_line[:200],
                    "content": stripped_line[:200],
                }
                matches.append(item)
                if len(matches) >= 100:
                    capped = True
                    break
        if capped:
            break

    files_with_conflicts = sorted(list(set(m["path"] for m in matches)))
    out: Dict[str, Any] = {
        "matches": matches,
        "conflicts": matches,
        "markers": matches,
        "count": len(matches),
        "files_with_conflicts": files_with_conflicts,
        "files_count": len(files_with_conflicts),
        "has_conflicts": len(matches) > 0,
        "capped": capped,
    }
    if glob_pattern:
        out["glob"] = glob_pattern

    if matches:
        detail = f"Found {len(matches)} conflict marker(s) across {len(files_with_conflicts)} file(s)"
        if capped:
            detail += " (capped at 100)"
    else:
        detail = "No conflict markers found"

    out = _bound_output(out, max_chars=15000)
    return (out, "success", detail)


def execute_project_tool(
    tool_name: str,
    raw_arguments: Any,
    snapshot: Dict[str, str],
) -> Tuple[str, ProjectChatTraceItem, Optional[Dict[str, Any]]]:
    start_time = time.perf_counter()

    # Parse raw arguments
    if isinstance(raw_arguments, str):
        try:
            args = json.loads(raw_arguments) if raw_arguments.strip() else {}
            if not isinstance(args, dict):
                args = {"_raw": raw_arguments}
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            err_msg = f"Invalid JSON in tool arguments for '{tool_name}': {exc}"
            return (
                json.dumps({"error": err_msg}),
                ProjectChatTraceItem(
                    tool=tool_name,
                    status="error",
                    detail=err_msg,
                    duration_ms=round(elapsed_ms, 2),
                ),
                None,
            )
    elif isinstance(raw_arguments, dict):
        args = raw_arguments
    else:
        args = {}

    try:
        if tool_name == "list_files":
            out, status, detail = handle_list_files(snapshot, args)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return (
                json.dumps(out),
                ProjectChatTraceItem(tool=tool_name, status=status, detail=detail, duration_ms=round(elapsed_ms, 2)),
                None,
            )
        elif tool_name == "read_file":
            out, status, detail = handle_read_file(snapshot, args)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return (
                json.dumps(out),
                ProjectChatTraceItem(tool=tool_name, status=status, detail=detail, duration_ms=round(elapsed_ms, 2)),
                None,
            )
        elif tool_name == "search_files":
            out, status, detail = handle_search_files(snapshot, args)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return (
                json.dumps(out),
                ProjectChatTraceItem(tool=tool_name, status=status, detail=detail, duration_ms=round(elapsed_ms, 2)),
                None,
            )
        elif tool_name == "inspect_project":
            out, status, detail = handle_inspect_project(snapshot, args)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return (
                json.dumps(out),
                ProjectChatTraceItem(tool=tool_name, status=status, detail=detail, duration_ms=round(elapsed_ms, 2)),
                None,
            )
        elif tool_name == "propose_changes":
            out, status, detail, proposal_obj = handle_propose_changes(snapshot, args)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return (
                json.dumps(out),
                ProjectChatTraceItem(tool=tool_name, status=status, detail=detail, duration_ms=round(elapsed_ms, 2)),
                proposal_obj,
            )
        elif tool_name == "analyze_python":
            out, status, detail = handle_analyze_python(snapshot, args)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return (
                json.dumps(out),
                ProjectChatTraceItem(tool=tool_name, status=status, detail=detail, duration_ms=round(elapsed_ms, 2)),
                None,
            )
        elif tool_name == "validate_json":
            out, status, detail = handle_validate_json(snapshot, args)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return (
                json.dumps(out),
                ProjectChatTraceItem(tool=tool_name, status=status, detail=detail, duration_ms=round(elapsed_ms, 2)),
                None,
            )
        elif tool_name == "check_conflicts":
            out, status, detail = handle_check_conflicts(snapshot, args)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return (
                json.dumps(out),
                ProjectChatTraceItem(tool=tool_name, status=status, detail=detail, duration_ms=round(elapsed_ms, 2)),
                None,
            )
        else:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            err_msg = f"Unknown tool '{tool_name}'. Available tools: list_files, read_file, search_files, inspect_project, propose_changes, analyze_python, validate_json, check_conflicts."
            return (
                json.dumps({"error": err_msg}),
                ProjectChatTraceItem(tool=tool_name, status="error", detail=err_msg, duration_ms=round(elapsed_ms, 2)),
                None,
            )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        err_msg = f"Tool execution failed for '{tool_name}': {type(exc).__name__}: {exc}"
        return (
            json.dumps({"error": err_msg}),
            ProjectChatTraceItem(tool=tool_name, status="error", detail=err_msg, duration_ms=round(elapsed_ms, 2)),
            None,
        )
