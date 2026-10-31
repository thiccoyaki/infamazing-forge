import webbrowser
import threading

import uvicorn

from forge import config as C


def main():
    url = f"http://localhost:{C.PORT}"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"infamazing-forge → {url}")
    uvicorn.run("app.server:app", host="127.0.0.1", port=C.PORT, log_level="warning")


if __name__ == "__main__":
    main()
