"""Import every model so SQLAlchemy metadata is complete."""
from app.models.core import *  # noqa: F401,F403
from app.models.people import *  # noqa: F401,F403
from app.models.academic import *  # noqa: F401,F403
from app.models.scheduling import *  # noqa: F401,F403
from app.models.crm import *  # noqa: F401,F403
from app.models.finance import *  # noqa: F401,F403
from app.models.ops import *  # noqa: F401,F403
