"""Launch the MSF Phantom QA web app.

Usage:  python run_app.py  [port]
Then open http://127.0.0.1:8777 in a browser.
"""

import sys

import uvicorn

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    uvicorn.run("phantom_qa.webapp.main:app", host="127.0.0.1", port=port,
                log_level="info")
