import os
import shutil
import subprocess
import sys
import tempfile
import time
import httpx


def main():
    agent_key = os.getenv("VAULTLINE_TEST_AGENT_KEY") or os.getenv("VAULTLINE_AGENT_KEY")
    if not agent_key or not agent_key.strip():
        print(
            "ERROR: VAULTLINE_TEST_AGENT_KEY or VAULTLINE_AGENT_KEY must be explicitly set in the environment.\n"
            "Silent default key fallback is disabled for security and consistency.",
            file=sys.stderr,
        )
        return 1
    agent_key = agent_key.strip()

    port = int(os.getenv("VAULTLINE_TEST_PORT", "8008"))
    base_url = f"http://127.0.0.1:{port}"

    # Use an isolated temporary directory for the test database to avoid deleting or colliding with existing databases
    test_db_dir = tempfile.mkdtemp(prefix="vaultline_itest_")
    db_path = os.path.join(test_db_dir, "test.db")

    backend_env = os.environ.copy()
    backend_env["VAULTLINE_USE_REAL_PAYPAL"] = "false"
    backend_env["VAULTLINE_DEMO_MODE"] = "true"
    backend_env["APP_ENV"] = "test"
    backend_env["VAULTLINE_AGENT_KEY"] = agent_key
    backend_env["VAULTLINE_DB_PATH"] = db_path

    print(f"Starting Vaultline FastAPI backend on {base_url} (isolated db: {db_path})...")
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(port), "--host", "127.0.0.1"],
        env=backend_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait for backend health check
        healthy = False
        for _ in range(30):
            try:
                r = httpx.get(f"{base_url}/health", timeout=1.0)
                if r.status_code == 200:
                    healthy = True
                    break
            except Exception:
                pass
            time.sleep(0.3)

        if not healthy:
            print("ERROR: FastAPI backend failed to start.", file=sys.stderr)
            server_proc.kill()
            out, err = server_proc.communicate()
            print("STDOUT:", out.decode(errors="replace"), file=sys.stderr)
            print("STDERR:", err.decode(errors="replace"), file=sys.stderr)
            return 1

        print(f"FastAPI backend healthy at {base_url}/health. Running MCP integration tests...")

        test_env = os.environ.copy()
        test_env["VAULTLINE_TEST_API_BASE_URL"] = base_url
        test_env["VAULTLINE_TEST_AGENT_KEY"] = agent_key
        test_env["VAULTLINE_TEST_CAPTURE_ID"] = "CAP_A"
        test_env["VAULTLINE_USE_REAL_PAYPAL"] = "false"

        # Run pytest inside mcp/
        mcp_dir = os.path.dirname(os.path.abspath(__file__))
        pytest_proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_integration.py", "-v"],
            cwd=mcp_dir,
            env=test_env,
        )
        return pytest_proc.returncode
    finally:
        print("Stopping FastAPI backend server...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        print("Backend server stopped.")

        # Clean up isolated test database directory
        if os.path.exists(test_db_dir):
            try:
                shutil.rmtree(test_db_dir, ignore_errors=True)
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
