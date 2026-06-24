"""Mock xtquant.xtdata (empty — we use baostock instead)"""

def download_history_data(*args, **kwargs):
    pass

def download_history_data2(*args, **kwargs):
    pass

def get_market_data(*args, **kwargs):
    return {}

def get_market_data_ex(*args, **kwargs):
    return {}
