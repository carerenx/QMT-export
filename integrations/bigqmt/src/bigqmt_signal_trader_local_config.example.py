# coding: utf-8
"""Copy to bigqmt_signal_trader_local_config.py in the QMT python directory."""
BIGQMT_ACCOUNT_ID = "YOUR_ACCOUNT_ID"
BIGQMT_ACCOUNT_TYPE = "STOCK"
BIGQMT_REDIS_CONFIG = {
    "transport": "zmq",
    "zmq": {"host": "127.0.0.1", "port": 15700,
            "bind_address": "tcp://127.0.0.1:15700"},
    "rpc_allow_order_methods": False,
    "rpc_process_in_listener": True,
    "rpc_listener_methods": ("*",),
    "rpc_background_threads": False,
    "schedule_adjust": True,
    "schedule_adjust_interval": "500nMilliSecond",
    "download_jobs_enabled": False,
    "exec_events_enabled": True,
}
