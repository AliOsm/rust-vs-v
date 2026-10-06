"""Build V's pinned portable compiler snapshot and apply the tested runtime changes."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = ROOT / ".toolchains/v"
COMPILER = CHECKOUT / "v"
LOCK = ROOT / "toolchains/v.lock.json"
STAMP = CHECKOUT / ".chat-toolchain.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, cwd=ROOT):
    subprocess.run(list(map(str, command)), cwd=cwd, check=True)


def checkout(directory, pin):
    if not (directory / ".git").exists():
        directory.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "-q", directory])
        run(["git", "remote", "add", "origin", pin["url"]], cwd=directory)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=directory,
                          capture_output=True, text=True)
    if head.returncode:
        run(["git", "fetch", "--quiet", "--depth=1", "origin", pin["commit"]], cwd=directory)
        run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=directory)
    elif head.stdout.strip() != pin["commit"]:
        raise RuntimeError(f"Unexpected revision in {directory}; preserve it before reinstalling")


def verify():
    if not STAMP.exists():
        raise RuntimeError("V is not set up. Run `mise run setup` first.")
    stamp = json.loads(STAMP.read_text())
    pins = json.loads(LOCK.read_text())
    assert stamp["lock_sha256"] == sha(LOCK), "V toolchain lock changed; reinstall the toolchain"
    assert stamp["compiler_sha256"] == sha(COMPILER), "V compiler changed"
    assert pins["runtime_patch_sha256"] == sha(ROOT / "toolchains/v-runtime.patch")
    for name, digest in pins["runtime_source_sha256"].items():
        assert sha(CHECKOUT / name) == digest, name


def main():
    if not os.environ.get("RUST_VS_V_RESOURCE_GUARD"):
        raise SystemExit("Run `mise run setup` to use the memory-limited build task")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("The pinned V reactor toolchain supports Linux x86_64")
    if STAMP.exists():
        verify()
        print("Pinned V compiler and runtime verified.")
        return
    pins = json.loads(LOCK.read_text())
    patch = ROOT / "toolchains/v-runtime.patch"
    assert sha(patch) == pins["runtime_patch_sha256"]
    for name, directory in (("v", CHECKOUT), ("vc", CHECKOUT / "vc"),
                            ("tcc", CHECKOUT / "thirdparty/tcc")):
        checkout(directory, pins[name])
    # vc/v.c is V's complete portable compiler. Build it directly, avoiding the
    # much larger memory peak of compiling the compiler again from V sources.
    run(["gcc", "--param", "ggc-min-expand=10", "--param", "ggc-min-heapsize=16384",
         "-DCUSTOM_DEFINE_v1_fallback", "-std=c99", "-w", "-o", COMPILER,
         CHECKOUT / "vc/v.c", "-lm", "-lpthread"], cwd=CHECKOUT)
    run([COMPILER, "version"])
    run(["git", "apply", "--check", patch], cwd=CHECKOUT)
    run(["git", "apply", patch], cwd=CHECKOUT)
    stamp = {"lock_sha256": sha(LOCK), "compiler_sha256": sha(COMPILER)}
    STAMP.write_text(json.dumps(stamp, indent=2) + "\n")
    verify()
    print("Pinned V compiler and reactor runtime ready.")


if __name__ == "__main__":
    main()
