"""
Supply chain delivery tools (recommendation, email).
Predict, diagnosis, and simulation are served via MCP (prediction_server.py).
"""

from .recommend_actions import recommend_actions
from .email_customers import fetch_delayed_orders_for_email

__all__ = [
    "recommend_actions",
    "fetch_delayed_orders_for_email",
]
