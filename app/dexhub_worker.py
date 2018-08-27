#
#
# (c) 2017 elias/vanissoft
#
# Bitshares comm
#
"""
"""




from config import *
import asyncio
import json
import arrow
import random
import hashlib
import base64
import pickle
import blockchain, accounts, ohlc_analysers, market_data
import passwordlock


WBTS = {}
Active_module = None
Assets_id = {}
Assets_name = {}
MDF = None

def init():
	"""
	Initialisation
	*
	:return:
	"""
	import os
	global Assets_id, Assets_name, MDF
	os.chdir('../data')
	with open('assets.pickle', 'rb') as h:
		tmp = pickle.load(h)
	Assets_id = {k:v for (k,v) in [(k,v[0]) for (k,v) in tmp.items()]}
	Assets_name = {v:k for (k,v) in [(k,v[0]) for (k,v) in tmp.items()]}
	#TODO: is this need?
	#blockchain.init()
	#Redisdb.bgsave()

	MDF = market_data.MarketDataFeeder()
	print("end")



def check_for_master_password():
	msg = None
	if Redisdb.get('master_hash') is None:
		msg = "Unlock with master password first."
	if msg is not None:
		Redisdb.rpush("datafeed", json.dumps({'module': "general", 'message': msg,'error': True}))
		return False
	return True





def privileged_connection(account_name):
	if not check_for_master_password():
		return
	if account_name not in WBTS or not WBTS[account_name].is_connected():
		tmp = accounts.account_list()
		wif = {x[0]: x[2] for x in tmp}
		WBTS[account_name] = BitShares(node=WSS_NODE, wif=wif[account_name])
	return WBTS[account_name]


class Operations_listener():

	def __init__(self):
		asyncio.get_event_loop().run_until_complete(self.do_operations())

	async def letmeuselocalcache(self, data):
		#TODO: prevent the use of cache if the settings have changed
		Redisdb.rpush("datafeed", json.dumps({'module': data['module'], 'uselocalcache': False}))

	async def get_balances(self, data):
		# TODO: another column for asset collateral
		bal1, margin_lock_BTS, margin_lock_USD = await blockchain.get_balances()
		if bal1 is None:
			Redisdb.rpush("datafeed", json.dumps({'module': 'general', 'message': "No account defined!", 'error': True}))
		else:
			Redisdb.rpush("datafeed", json.dumps({'module': data['module'], 'balances': bal1,
												'margin_lock_BTS': margin_lock_BTS,
												'margin_lock_USD': margin_lock_USD}))

	async def get_tradestats_token(self, data):
		stats = market_data.Stats()
		Redisdb.rpush("datafeed", json.dumps({'module': data['module'],
							'stats_token': stats.stats_by_token[['asset_name', 'ops', 'volume', 'ops_day', 'volume_day']][:100].to_json(orient='values')}))

	async def get_tradestats_pair(self, data):
		stats = market_data.Stats()
		Redisdb.rpush("datafeed", json.dumps({'module': data['module'],
							'stats_pair': stats.stats_by_pair[['pair_text', 'pair', 'pays_amount', 'receives_amount', 'price']][:200].to_json(orient='values')}))

	async def get_tradestats_account(self, data):
		stats = market_data.Stats()
		Redisdb.rpush("datafeed", json.dumps({'module': data['module'],
							'stats_account': stats.stats_by_account[['account_id', 'pair']][:100].to_json(orient='values')}))

	async def get_tradestats_accountpair(self, data):
		stats = market_data.Stats()
		Redisdb.rpush("datafeed", json.dumps({'module': data['module'],
							'stats_accountpair': stats.stats_by_account_pair[['account_id', 'pair_text', 'pair']][:100].to_json(orient='values')}))

	async def get_orderbook(self, data):
		buys = await blockchain.get_orderbook(data)
		Redisdb.rpush("datafeed", json.dumps({'module': data['module'], 'orderbook': {'market': data['market'], 'date': arrow.utcnow().isoformat(), 'data': buys}}))


	async def open_positions(self, data):
		rtn = await blockchain.open_positions()
		Redisdb.rpush("datafeed", json.dumps({'module': data['module'], 'open_positions': rtn}))

	async def get_market_trades(self, data):
		movs = await blockchain.get_market_trades(data)
		if movs is not None and len(movs) > 0:
			movs.sort(key=lambda x: x[0])
			movs = movs[-100:]
			Redisdb.rpush("datafeed", json.dumps({'module': data['module'], 'market_trades': {'market': data['market'], 'data': movs}}))

	async def get_last_trades(self, data):
		def response(data):
			pass
		a = ohlc_analysers.Analyze(range=(arrow.utcnow().shift(days=-7), arrow.utcnow()), pairs=[data['market']], MDF=MDF, callback=response)
		tmp = data['market'].split('/')
		mkt = Assets_name[tmp[0]]+':'+Assets_name[tmp[1]]
		if 'dfo' not in a.__dict__:
			return
		a.ohlc(timelapse="1h", fill=False)
		rdates = a.df_ohlc['time'].dt.to_pydatetime().tolist()
		rdates = [x.isoformat() for x in rdates]
		movs = [x for x in zip(rdates,
					 a.df_ohlc.priceopen.tolist(), a.df_ohlc.priceclose.tolist(),
					 a.df_ohlc.pricelow.tolist(), a.df_ohlc.pricehigh.tolist(),
					 a.df_ohlc.amount_base.tolist())]
		Redisdb.rpush("datafeed",
					  json.dumps({'module': Active_module, 'market_trades': {'market': data['market'], 'data': movs}}))

	async def account_list(self, dummy):
		accs = accounts.account_list()
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'settings_account_list': accs}))

	async def account_new(self, data):
		accs = accounts.account_new(data)
		Redisdb.set("settings_accounts", json.dumps(accs))
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'settings_account_list': accs}))

	async def account_delete(self, data):
		accs = accounts.account_delete(data)
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'settings_account_list': accs}))
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'message': "Account deleted"}))

	async def save_misc_settings(self, dat):
		rtn = Redisdb.get("settings_misc")
		if rtn is None:
			settings = {}
		else:
			settings = json.loads(rtn.decode('utf8'))
		for k in dat['data']:
			if k == "master_password":
				if dat['data'][k].lstrip() != '':
					passwordlock.store_mp(dat['data'][k])
			else:
				settings[k] = dat['data'][k]
		Redisdb.set("settings_misc", json.dumps(settings))
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'settings_misc': settings}))
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'message': "settings saved"}))

	async def get_settings_misc(self, dummy):
		rtn = Redisdb.get("settings_misc")
		if rtn is None:
			settings = {}
		else:
			settings = json.loads(rtn.decode('utf8'))
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'settings_misc': settings}))

	async def settings_prefs_bases(self, param):
		rtn = Redisdb.get("settings_prefs_bases")
		if rtn is None:
			settings = []
		else:
			settings = json.loads(rtn.decode('utf8'))
		# most used tokens list
		stats = market_data.Stats()

		tlist = stats.stats_by_token[['asset_name']]['asset_name'][:20]
		tlist = tlist.tolist()
		if 'orderbyops' in param:
			settings = tlist
		else:
			for t in tlist:
				if t not in settings:
					settings.append(t)
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'settings_prefs_bases': settings}))

	async def save_settings_bases(self, dat):
		Redisdb.set("settings_prefs_bases", json.dumps(dat['data']))
		Redisdb.rpush("datafeed", json.dumps({'module': 'general', 'message': "Preferences saved.", 'error': False}))

	async def order_delete(self, data):
		# need account 
		conn = privileged_connection(data['account'])
		if conn is None:
			return
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'message': "Order {0} delete?".format(data['id'])}))
		blockchain.order_delete(id=data['id'], conn=conn, account=data['account'])

	async def master_unlock(self, dat):
		if passwordlock.check_mp(dat['data']):
			Redisdb.rpush("datafeed", json.dumps({'module': 'general', 'master_unlock': {'message': 'unlocked', 'error': False}}))
			Redisdb.rpush("datafeed", json.dumps({'module': 'general', 'message': "Unlocked", 'error': False}))
			Redisdb.rpush("datafeed", json.dumps({'module': 'general', 'reload': 1}))
		else:
			Redisdb.rpush("datafeed", json.dumps({'module': 'general', 'master_unlock': {'message': "password does not match", 'error': True}}))
			Redisdb.rpush("datafeed", json.dumps({'module': 'general', 'message': "Password does not match", 'error': True}))

	async def marketpanels_savelayout(self, dat):
		Redisdb.set("MarketPanels_layout", dat['data'])

	async def marketpanels_loadlayout(self, dat):
		default = [["OPEN.ETH/BTS", 1]]
		rtn = Redisdb.get("MarketPanels_layout")
		if rtn is None:
			layout = default
		else:
			layout = json.loads(rtn.decode('utf8'))
			if len(layout) == 0:
				layout = default
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'marketpanels_layout': layout}))

	async def ping(self):
		Redisdb.rpush("datafeed", json.dumps({'module': Active_module, 'data': 'pong'}))

	async def rpc_ping(self, dummy):
		return await blockchain.read_ticker('BTS/CNY', force=True)

	async def account_tradehistory(self, dat):
		accounts.trade_history([x[0] for x in accounts.account_list()], MDF, dat['module'])

	async def marketdatafeeder_step(self, dummy):
		MDF.step()


	async def do_ops(self, op):
		"""
		Process the enqueued operations.
		:param op:
		:return:
		"""
		global Active_module
		Active_module = Redisdb.get('Active_module').decode('utf8')

		try:
			dat = json.loads(op.decode('utf8'))
		except Exception as err:
			print(err.__repr__())
			return
		# calls method
		print("calling:", dat['call'])
		fn = getattr(self, dat['call'], None)
		if fn is not None:
			await fn(dat)
		else:
			print("error: ", dat['call'], 'not defined')


	async def do_operations(self):

		while True:
			op = Redisdb.lpop("operations")
			if op is None:
				op = Redisdb.lpop("operations_bg")
				if op is None:
					await asyncio.sleep(.01)
					continue

			# show queue debug in client
			status = {'operations': [x.decode('utf8') for x in Redisdb.lrange('operations', 0, 999)],
						'operations_bg': [x.decode('utf8') for x in Redisdb.lrange('operations_bg', 0, 999)]}
			Redisdb.rpush("datafeed", json.dumps({'module': 'general', 'status': status}))
			# -------------

			await self.do_ops(op)
			# send info of queues




if __name__ == "__main__":
	import sys
	init()
	if len(sys.argv) > 1:
		if 'blockchain_listener' in sys.argv[1]:
			# TODO: realtime listener?
			#blockchain_listener()
			pass
		elif 'operations_listener' in sys.argv[1]:
			Operations_listener()
	else:
		# runs in bg, invoked in main
		Operations_listener()
