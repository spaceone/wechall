#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import time
import re
from socket import gethostname
from optparse import OptionParser
import readline

from circuits import handler, Component, Debugger, Timer, Event
from circuits import __version__ as systemVersion

from circuits.io import stdin

from circuits.net.events import connect
from circuits.net.sockets import TCPClient

from circuits.protocols.irc import IRC, PRIVMSG, USER, NICK, JOIN
from circuits.protocols.irc.utils import strip, irc_color_to_shell_escape


USAGE = "%prog [options] host [port]"
VERSION = "%prog v" + systemVersion


class Shadowbot(Component):

	channel = "ircclient"

	def init(self, host, port=6667, opts=None):
		self.host = host
		self.port = port
		self.opts = opts
		self.hostname = gethostname()

		self.nick = opts.nick
		self.ircchannel = opts.channel
		self.master = opts.master

		TCPClient(channel=self.channel).register(self)
		IRC(channel=self.channel).register(self)
		Timer(60, Event.create('main_loop'), persist=True).register(self)
		Timer(30, Event.create('execute_action'), persist=True).register(self)
#		Timer(10, Event.create('lamb', '#flee'), persist=True).register(self)

		if opts.debug:
			Debugger().register(self)

		self.inventory = {}
		self.known_places = {}
		self.commands = []
		self.next_action = ''
		self.previous_action = 'none'
		self.party_status = ''
		self.current_status = ''
#		self.do_action('explore')
#		self.do_action('explore')

	def ready(self, component):
		self.fire(connect(self.host, self.port))

	def connected(self, host, port):
		print("Connected to %s:%d" % (host, port))

		nick = self.nick
		hostname = 'box1'
		name = "%s!shadowcrap bot using circuits" % (nick,)

		self.fire(NICK(nick))
		self.fire(USER(nick, nick, hostname, name))

	def disconnected(self):
		print("Disconnected from %s:%d" % (self.host, self.port))
		raise SystemExit(0)

	def numeric(self, source, numeric, *args):
		if numeric == 1:
			self.fire(JOIN(self.ircchannel))
		elif numeric == 433:
			self.nick = newnick = "%s_" % self.nick
			self.fire(NICK(newnick))

	def join(self, source, channel):
		if source[0].lower() == self.nick.lower():
			print("Joined %s" % channel)
		else:
			print("--> %s (%s) has joined %s" % (source[0], "@".join(source[1:]), channel))

	def notice(self, source, target, message):
		self.privmsg(source, target, message)

	def privmsg(self, source, target, message):
		if target[0] == "#":
			print("<%s> %s" % (source[0], irc_color_to_shell_escape(message)))
		elif source[0] in ('Lamb3', self.master) and target == self.nick:
			self.parse_message(source, message)
		else:
			print("-%s- %s" % (source[0], irc_color_to_shell_escape(message)))

	def get_info(self):
		yield 'Current action: %s; Party status: %s' % (self.next_action, self.party_status)
		yield 'Status: %s' % (self.current_status,)
		yield 'Inventory:'
		inv = ''.join('%s %s ; ' % (key, val) for key, val in sorted(self.inventory.iteritems()))
		n = 170
		for line in [inv[i:i + n] for i in range(0, len(inv), n)]:
			yield line

	def parse_message(self, source, message):
		raw = strip(message, True)
		print("-%s- %s" % (source[0], irc_color_to_shell_escape(message)))
		if source[0] == self.master:
			return self.parse_master(message)

		if 'You respawn' in message:
			self.lamb('#p')
			self.do_action('sleep')
		if 'Cmds: ' in message:
			self.commands = raw.strip().strip('.').split(',')
		if 'Your parties HP:' in message:
			self.heal_hp(raw)
		if 'You meet' in message:  # TODO: detect if human / npc, say bye, check quests
			if 'tehron' in message:
				self.lamb('#bye')
				self.lamb('#say bye')
				return
			if '{' not in message:
				self.lamb('#say invite')
				#self.lamb('#say temple')
				self.lamb('#bye', 2)
		if 'says: "Hello chummer. Are you on a' in message:
			self.lamb('#bye', 4)
		if 'You ENCOUNTER' in raw:
			self.lamb('#flee')
			self.lamb('#p')
			e = 1
			if message.count('\x02-') > 1:
				# kill the weakest first!
				line = message.split('\x02')[3:][::2]
				e = min([map(int, line[i:i + 2][::-1]) for i in range(0, len(line), 2)])[1]
			self.lamb('## %d' % (e,))
		if 'With karma you can #lvlup' in raw:
			self.lamb('#s')
			self.lamb('#lvlup')
		if 'You continue ' in raw:
			self.lamb('#p')
			self.drop_things()
		if 'You cannot move because' in message and 'overloaded' in message:
			self.drop_things()
		if 'You received' in message:
			self.lamb('#i')
		#if 'reached level 21' in raw:
		#	self.do_action('goto Hotel;sleep')
		#	self.do_action('goto Subway;travel Chicago')
		if raw.startswith('Known Places in'):
			self.known_places = dict(x.split('-', 1) for x in raw.strip('.').split(': ', 1)[-1].split(', '))
		if message.startswith('You are'):
			if 'are exploring' in raw:
				self.party_status_changed('explore')
			if 'are fighting' in raw:
				self.party_status_changed('fighting')
				self.lamb('#flee')
			if 'are outside' in raw:
				self.party_status_changed('outside')
			if 'are going' in raw:
				self.party_status_changed('going')
			if 'are sleeping inside of' in raw:
				self.party_status_changed('sleeping')
			if 'are inside' in raw:
				self.party_status_changed('inside')
			if 'are travelling' in raw:
				self.party_status_changed('travelling')
		if message.startswith('Your Inventory'):
			self.get_inventory(raw)
		if message.startswith('male '):
			self.parse_status(raw)

	def parse_status(self, message):
		self.current_status = dict([[y.strip('\x02') for y in x.split(':', 1)] for x in re.split('[ ,]', message.strip('.')) if ':' in x])
		x, y = map(float, self.current_status['Weight'].replace('kg', '').split('/', 1))
		if x > y:
			self.drop_things()

	def parse_master(self, message):
		if message.startswith('cmd '):
			self.lamb(message[3:])
		elif message.startswith('action '):
			self.do_action(message[7:])
		elif message.startswith('info'):
			for info in self.get_info():
				self.fire(PRIVMSG(self.master, info))
		elif message.startswith('drop'):
			self.drop_things()

	def party_status_changed(self, status, *args):
		self.party_status = status

	def get_inventory(self, raw):
		c, p = map(int, raw.split(': ')[0].split('page ')[-1].split('/'))
		if c == 1:
			self.inventory.clear()
		self.inventory.update(dict([int(a), b] for a, b in [x.split('-', 1) for x in raw[:-1].split(': ', 1)[-1].split(', ')]))
		if c == 1:
			for i in range(2, p + 1):
				self.lamb('#i %d' % i)
		if c == p:
			for line in self.get_info():
				print line

	def drop_things(self):
		drop_items = ('Ammo_4mm', 'Ammo_9mm', 'Ammo_11mm', 'Skates', 'EmptyBottle', 'Shorts', 'Shirt', 'Shoes', 'Sneakers', 'FineRobe', 'Club', 'Cap', 'Trousers', 'TankTop', 'LargeAxe', 'SmithHammer', 'HaukAxe', 'Booze', 'RawMeat', 'Fur')
		drop_items_substring = [
			'Blouse', 'ElvenStaff', 'Cap', 'Tinfoil', 'Axe_with', 'NinjaSword', 'SamuraiMask', 'BikerBelt', 'BaseballBat', 'Trousers_with', 'ChainBoots', 'BikerBoots', 'BikerHelmet', 'Mace', 'LeatherLegs',
			'StuddedLegs', 'ElvenShorts', 'StuddedLegs', 'LeatherVest', 'SportBow_', '5ChrisClub', 'SkiMask', 'ElvenRobe', 'BikerJacket', 'KevlarLegs', 'KnightLegs', 'Yugake', 'BrassKnuckles', 'TShirt']
		if self.party_status == 'fighting' or self.commands and 'dr' not in self.commands:
			return
		dropped = False
		for x, i in self.inventory.items():
			i, _, c = re.search('([^(]+)(\((\d+)\))?', i).groups()
			c = int(c) if c else ''
			if i in drop_items or any(f in i for f in drop_items_substring):
				self.lamb(('#drop %s %s' % (i, c)).strip())
				dropped = True
			if i.startswith('Cop'):
				self.lamb('#mount push %s' % (i,))
		if dropped:
			self.lamb('#i')
			self.lamb('#s', 2)
		return
		for x, i in self.inventory.items():
			if any(f in i for f in ['Booze', 'FirstAid', 'Stimpatch', 'Coke', 'Milk', 'Apple', 'Bacon', 'Cake', 'Easteregg', 'Fish', 'StrengthPotion', ]):
				self.lamb('#use %s' % x)
				self.lamb('#i')
				break

	def heal_hp(self, raw):
		users = [x.split('-', 1)[-1] for x in raw[:-1].split('Your parties HP: ', 1)[-1].split(', ')]
		for user in users:
			x, y = map(float, re.match('.*\((.*)\)', user).group(1).split('/'))
			print '#### HP', x, x < 10
			if (x < 6 if self.party_status == 'fighting' else x < 10):
				name = user.split('(')[0]
				print '### CRITICAL HEALTH !', name
				for i in ('SmallFirstAid', 'Milk', 'Coke', 'Apple', 'SmallBeer', 'LargeBeer', 'FirstAid', 'Stimpatch', ):
					if any(i in v for v in self.inventory.items()):
						self.lamb('#use %s %s' % (i, name))
						self.lamb('#hp', 2)
						break
			#if x < 8:
			#	self.do_action('goto Hotel;sleep')
			#else:
			#	if self.next_action == 'goto Hotel;sleep':
			#		self.action_done()

	def do_action(self, action):
		self.previous_action = self.next_action
		self.next_action = action

	def action_done(self):
		self.next_action = self.previous_action

	def lamb(self, x, s=None):
		if not s:
			self.send_lamb(x)
		else:
			self.execute_in('send_lamb', s, x)

	def execute_in(self, method, seconds, *args, **kwargs):
		Timer(seconds, Event.create(method, *args, **kwargs)).register(self)

	def send_lamb(self, x):
		print '<space>:', x
		self.fire(PRIVMSG('Lamb3', x))

	def main_loop(self):
		self.lamb('#i')
		self.lamb('#hp', 1)
		self.lamb('#s', 1)
		#self.lamb('#a', 1)
		#self.lamb('#sk', 1)
		self.lamb('#p', 1)
#		if self.party_status not in ('sleeping', 'fighting', 'explore'):
#			self.lamb('#explore')

	def execute_action(self):
		action, _, what = self.next_action.partition(';')
		#print
		#print action, ';', what, self.party_status, self.known_places
		#print
		if not action:
			self.action_done()
		elif action == 'explore':
			self.lamb('#explore')
			if self.party_status == 'explore':
				self.step_action('exploring;' + what)
		elif action == 'exploring':
			if self.party_status not in ('explore', 'fighting'):
				self.step_action(what)
		elif action.startswith('goto '):
			location = action.split('goto ', 1)[-1]
			self.lamb('#kp')
			self.step_action('!' + self.next_action)
		elif action.startswith('!goto '):
			if self.party_status == 'fighting':
				return
			location = action.split('!goto ', 1)[-1]
			if location in self.known_places.values():
				self.lamb('#g %s' % (location,))
				self.next_action = 'gooing;' + what
			elif 'Exit' in self.known_places.values():
				self.step_action('goto Exit;leave;' + self.next_action)
			else:
				self.next_action = self.next_action.lstrip('!')
				self.report_error('unkown location: %s of %r' % (location, self.known_places))
		elif action == 'gooing':
			if self.party_status in ('outside', 'inside'):
				self.step_action(what)
		elif action == 'sleep':
			self.lamb('#p')
			if self.party_status == 'inside':
				self.lamb('#sleep')
				self.lamb('#p', 1)
				self.execute_in('execute_action', 2)
			elif self.party_status == 'sleeping':
				self.step_action(what)
			else:
				self.report_error('command sleep: %s' % (self.party_status,))
		elif action in ('leave', 'enter', 'exit') or action.startswith('talk '):
			self.lamb('#' + action)
			self.step_action(what)

	def step_action(self, action):
		self.next_action = action
		self.execute_in('execute_action', 2)

	@handler("read", channel="stdin")
	def stdin_read(self, data):
		data = data.strip().decode("utf-8")

		print("<{0:s}> {1:s}".format(self.nick, data))
		self.fire(PRIVMSG('Lamb3', data))

	def report_error(self, message):
		print '############ ERROR #############'
		print message
		self.fire(PRIVMSG(self.master, 'ERROR: ' + message))

	@classmethod
	def parse_options(cls):
		parser = OptionParser(usage=USAGE, version=VERSION)

		parser.add_option(
			"-n", "--nick",
			action="store", default=os.environ["USER"], dest="nick",
			help="Nickname to use"
		)

		parser.add_option(
			"-m", "--master",
			action="store", default="spaceone", dest="master",
			help="Person who can control this bot"
		)

		parser.add_option(
			"--without-readline",
			action="store_false", default=False, dest="without_readline",
			help="Disable readline propmpt and tab completion",
		)

		parser.add_option(
			"-d", "--debug",
			action="store_true", default=False, dest="debug",
			help="Enable debug verbose logging",
		)

		parser.add_option(
			"-c", "--channel",
			action="store", default="#circuits", dest="channel",
			help="Channel to join"
		)

		opts, args = parser.parse_args()

		if len(args) < 1:
			parser.print_help()
			raise SystemExit(1)

		return opts, args

	@classmethod
	def main(cls):
		opts, args = cls.parse_options()

		host = args[0]
		if len(args) > 1:
			port = int(args[1])
		else:
			port = 6667

		# Configure and run the system.
		client = cls(host, port, opts=opts)
		if opts.without_readline:
			stdin.register(client)
			client.run()
		else:
			readline.parse_and_bind("tab: complete")
			client.start()
			time.sleep(0.3)
			while client.running:
				client.stdin_read(raw_input('>>> '))


if __name__ == "__main__":
	Shadowbot.main()
