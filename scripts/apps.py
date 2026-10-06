"""Build and validate both chat apps with bounded memory and serial compilation."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from setup_v import COMPILER, verify as verify_v_dependencies

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build"
BINARIES = {
    "rust": BUILD / "rust/release/rust-chat",
    "v": BUILD / "v/v-chat",
}


def run(command, **kwargs):
    subprocess.run(list(map(str, command)), cwd=kwargs.pop("cwd", ROOT), check=True, **kwargs)


def cargo(command, *args):
    run(["cargo", command, "--locked", "--release", "--manifest-path", ROOT / "rust-app/Cargo.toml",
         "--target-dir", BUILD / "rust", "-j", "1", *args])


def compile_v(target, output, *, release):
    verify_v_dependencies()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Split code generation and GCC so their peak memory is not simultaneous.
    with tempfile.TemporaryDirectory(prefix="v-build-", dir=BUILD) as directory:
        project = Path(directory) / "c"
        flags = ["-new-compiler", "-cc", "gcc", "-gc", "boehm", "-nocache"]
        if release:
            flags += ["-prod"]
        flags += ["-cflags", "--param ggc-min-expand=10 --param ggc-min-heapsize=16384"]
        run([COMPILER, *flags, "-path", COMPILER.parent / "vlib",
             "-generate-c-project", project, target], cwd=COMPILER.parent,
            env={**os.environ, "VEXE": str(COMPILER)})
        run(["sh", "build.sh"], cwd=project)
        shutil.copy2(project / (target.stem if target.is_file() else target.name), output)


def build(language):
    if language in ("rust", "all"):
        cargo("build")
    if language in ("v", "all"):
        compile_v(ROOT / "v-app", BINARIES["v"], release=True)


def fmt(*, check=False):
    run(["cargo", "fmt", "--manifest-path", ROOT / "rust-app/Cargo.toml", *(["--check"] if check else [])])
    # Formatting needs no experimental library overlay; use mise's stable V.
    run(["v", "fmt", "-verify" if check else "-w", *sorted((ROOT / "v-app").glob("*.v"))])


def check():
    fmt(check=True)
    cargo("clippy", "--all-targets", "--", "-D", "warnings")
    executable = BUILD / "v/frame-tests"
    compile_v(ROOT / "v-app/frame_json_view_test.v", executable, release=False)
    run([executable])


async def test(*, browser=False):
    sys.path.insert(0, str(ROOT / "tests"))
    import common
    common.BINARIES.update(BINARIES)
    output = BUILD / "validation"
    output.mkdir(parents=True, exist_ok=True)
    if browser:
        from test_browser import main
        await main(argparse.Namespace(apps=list(BINARIES), output=str(output / "browser")))
        return

    from test_contract import check as contract
    from test_frames import check as frames
    from test_sessions import check as sessions
    from test_json_buffers import check as buffers
    from test_ownership import check as copies
    from test_reactor import check as reactor
    from test_reactor_lifecycle import main as lifecycle

    results = []
    for app in BINARIES:
        for name, suite in (("contract", contract), ("frames", frames), ("sessions", sessions),
                            ("json-buffers", buffers), ("payload-ownership", copies)):
            result = await suite(app)
            assert result["passed"], result
            results.append({"app": app, "suite": name, "result": result})
            print(app, name, "PASS", flush=True)
            (output / "protocol.json").write_text(json.dumps(results, indent=2) + "\n")
    result = await reactor("v")
    assert result["passed"], result
    (output / "reactor.json").write_text(json.dumps(result, indent=2) + "\n")
    # The lifecycle suite requires a new directory on every invocation.
    with tempfile.TemporaryDirectory(prefix="lifecycle-", dir=output) as directory:
        await lifecycle(argparse.Namespace(app="v", rounds=4, output=str(Path(directory) / "results")))
        shutil.copy2(Path(directory) / "results/lifecycle.json", output / "lifecycle.json")
    print("V reactor protocol and shutdown checks PASS", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "fmt", "check", "test", "browser"))
    parser.add_argument("language", nargs="?", choices=("rust", "v", "all"), default="all")
    args = parser.parse_args()
    if not os.environ.get("RUST_VS_V_RESOURCE_GUARD"):
        parser.error("Use the mise tasks so builds and checks run inside the resource guard")
    if os.environ.get("VFLAGS"):
        parser.error("Unset VFLAGS to use the pinned V build configuration")
    BUILD.mkdir(exist_ok=True)
    if args.action == "build":
        build(args.language)
    elif args.action == "fmt":
        fmt()
    elif args.action == "check":
        check()
    else:
        asyncio.run(test(browser=args.action == "browser"))


if __name__ == "__main__":
    main()
