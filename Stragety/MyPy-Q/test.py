from xtquant import xttrader
t = xttrader.XtQuantTrader('127.0.0.1', 58610, None)
t.start()
print('连接结果:', t.connect())  # 返回0才是成功


from xtquant import xtdata

# 连接 MiniQMT
xtdata.connect()

# 下载历史数据
xtdata.download_history_data('601869.SH', '1d', '20240101', '20260806')

# 读取历史数据
data = xtdata.get_market_data(
    field_list=['open', 'high', 'low', 'close', 'volume'],
    stock_list=['601869.SH'], period='1d', dividend_type='front_ratio'
)

# 实时行情
tick = xtdata.get_full_tick(['601869.SH'])
price = tick['601869.SH']['lastPrice']
print('601869.SH 实时价格:', price)
