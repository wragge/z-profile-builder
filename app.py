import os
from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    session,
    send_from_directory,
)
from .prepare_data import *
import subprocess
import shutil
from celery import Celery, Task, shared_task, states
from celery.result import AsyncResult
from requests_oauthlib import OAuth1Session

load_dotenv()


def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app


# creates a Flask object
app = Flask(__name__)

app.config.from_mapping(
    CELERY=dict(
        broker_url="redis://localhost",
        result_backend="redis://localhost",
        task_ignore_result=True,
    ),
)

celery_app = celery_init_app(app)


app.secret_key = os.getenv("ZPROFILE_SECRET_KEY")
client_key = os.getenv("ZOTERO_CLIENT_KEY")
client_secret = os.getenv("ZOTERO_CLIENT_SECRET")

request_token_url = "https://www.zotero.org/oauth/request"
access_token_url = "https://www.zotero.org/oauth/access"
authorization_url = "https://www.zotero.org/oauth/authorize"

env = os.getenv("FLASK_ENV", "production")

if env == "development":
    callback_uri = "http://127.0.0.1:5000/z-profile/build"
else:
    callback_uri = "http://wraggelabs.com/z-profile/build"


@shared_task(ignore_result=False)
def build(zotero_id, zotero_key):
    print(os.getcwd())
    shutil.copytree(
        "z_profile/z-profile", f"z_profile/sites/{zotero_id}", dirs_exist_ok=True
    )
    builder = ZProfileBuilder(
        zotero_id, zotero_key, output_path=f"z_profile/sites/{zotero_id}"
    )
    builder.build_site()
    res = subprocess.run(["zola", "--root", f"z_profile/sites/{zotero_id}", "build"])
    shutil.make_archive(
        f"z_profile/file_downloads/zprofile-{zotero_id}",
        "zip",
        f"z_profile/sites/{zotero_id}/public",
    )
    shutil.rmtree(f"z_profile/sites/{zotero_id}")
    return zotero_id


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login")
def manual_login():
    return render_template("login.html")


@app.route("/zotero-login")
def oauth_login():
    oauth_session = OAuth1Session(
        client_key, client_secret=client_secret, callback_uri=callback_uri
    )
    response = oauth_session.fetch_request_token(request_token_url)
    session["resource_owner_key"] = response["oauth_token"]
    session["resource_owner_secret"] = response["oauth_token_secret"]
    login_url = oauth_session.authorization_url(authorization_url)
    login_url += "&library_access=1&notes_access=1"
    return redirect(login_url)


@app.route("/build", methods=["GET", "POST"])
def start_build():
    if session.get("authorised"):
        zotero_id = session["zotero_id"]
        zotero_key = session["zotero_key"]
    elif "oauth_token" in request.args:
        oauth_session = OAuth1Session(
            client_key,
            client_secret=client_secret,
            resource_owner_key=session["resource_owner_key"],
            resource_owner_secret=session["resource_owner_secret"],
        )
        oauth_session.parse_authorization_response(request.url)
        response = oauth_session.fetch_access_token(access_token_url)
        zotero_key = response["oauth_token"]
        zotero_id = response["userID"]
        session["zotero_key"] = zotero_key
        session["zotero_id"] = zotero_id
        session["authorised"] = True
    elif "zotero_key" in request.form:
        zotero_id = request.form.get("zotero_id")
        zotero_key = request.form.get("zotero_key")
    result = build.delay(zotero_id, zotero_key)
    return render_template("result.html", result_id=result, user_id=zotero_id)


@app.get("/result/<id>")
def task_result(id: str) -> dict[str, object]:
    result = AsyncResult(id)
    return {
        "ready": result.ready(),
        "successful": result.successful(),
        "value": str(result.result) if result.ready() else None,
    }


@app.route("/downloads/<filename>")
def download_file(filename):
    return send_from_directory("file_downloads", filename)
