from app.bootstrap import create_app
from app.infrastructure.settings import Settings

app = create_app(Settings())
