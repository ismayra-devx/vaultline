import os
import subprocess
import sys
import time
import httpx

def main():
    port = 8008
    base_url = f"http://127.0.0.1:{port}"
    agent_key = "vl_agent_secret_key_abc123"

    backend_env = os.environ.copy()
    backend_env["VAULTLINE_USE_REAL_PAYPAL"] = "false"
    backend_env["VAULTLINE_DB_PATH"] = "vaultline_itest.db"

    # Clean previous itest db if present
    for fname in ["vaultline_itest.db", "vaultline_itest.db-wal", "vaultline_itest.db-shm"]:
        if os.path.exists(fname):
            try:
                os.remove(fname)
            except OSError:
                pass

    print(f"Starting Vaultline FastAPI backend on {base_url}...")
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
            print("ERROR: FastAPI backend failed to start.")
            server_proc.kill()
            out, err = server_proc.communicate()
            print("STDOUT:", out.decode(errors="replace"))
            print("STDERR:", err.decode(errors="replace"))
            sys.exit(1)

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

        for fname in ["vaultline_itest.db", "vaultline_itest.db-wal", "vaultline_itest.db-shm"]:
            if os.path.exists(fname):
                try:
                    os.remove(fname)
                except OSError:
                    pass

if __name__ == "__main__":
    sys.exit(main())

