# ###################################################
# Copyright (C) 2008-2017 The Unknown Horizons Team
# team@unknown-horizons.org
# This file is part of Unknown Horizons.
#
# Unknown Horizons is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the
# Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
# ###################################################

import traceback

from fife import fife

from horizons.command.unit import Act
from horizons.component.selectablecomponent import SelectableComponent
from horizons.constants import LAYERS
from horizons.gui.mousetools.navigationtool import NavigationTool
from horizons.util.worldobject import WorldObject, WorldObjectNotFound


class SelectionTool(NavigationTool):
	_SELECTION_RECTANGLE_NAME = "_select" # GenericRenderer objects are sorted by name, so first char is important

	def __init__(self, session):
		super().__init__(session)
		self.deselect_at_end = True # Set this to deselect selections while exiting SelectionTool

	def remove(self):
		# Deselect if needed while exiting
		if self.deselect_at_end:
			selectables = self.filter_selectable(self.session.selected_instances)
			for i in self.filter_component(SelectableComponent, selectables):
				i.deselect()
		super().remove()

	def is_selectable(self, entity):
		# also enemy entities are selectable, but the selection representation will differ
		return entity.has_component(SelectableComponent)

	def filter_component(self, component, instances):
		"""Only get specific component from a list of world objects"""
		return [instance.get_component(component) for instance in instances]

	def filter_selectable(self, instances):
		"""Only keeps selectables from a list of world objects"""
		return list(filter(self.is_selectable, instances))

	def is_owned_by_player(self, instance):
		"""Returns boolean if single world object is owned by local player"""
		return instance.owner is not None and \
			hasattr(instance.owner, "is_local_player") and \
			instance.owner.is_local_player

	def filter_owner(self, instances):
		"""Only keep instances belonging to the user. This is used for multiselection"""
		return [i for i in instances if self.is_owned_by_player(i)]

	def fife_instance_to_uh_instance(self, instance):
		"""Visual fife instance to uh game logic object or None"""
		i_id = instance.getId()
		if i_id == '':
			return None
		try:
			return WorldObject.get_object_by_id(int(i_id))
		except WorldObjectNotFound:
			return None

	def mouseDragged(self, evt):
		if evt.getButton() == fife.MouseEvent.LEFT and hasattr(self, 'select_begin'):
			x, y = self.select_begin
			xx, yy = evt.getX(), evt.getY()
			do_multi = ((x - xx) ** 2 + (y - yy) ** 2) >= 10 # from 3px (3*3 + 1)
			self.session.view.renderer['GenericRenderer'].removeAll(self.__class__._SELECTION_RECTANGLE_NAME)
			if do_multi:
				# draw a rectangle
				xmin, xmax = min(x, xx), max(x, xx)
				ymin, ymax = min(y, yy), max(y, yy)
				a = fife.Point(xmin, ymin)
				b = fife.Point(xmax, ymin)
				c = fife.Point(xmax, ymax)
				d = fife.Point(xmin, ymax)
				self._draw_rect_line(a, b)
				self._draw_rect_line(b, c)
				self._draw_rect_line(d, c)
				self._draw_rect_line(d, a)
				area = fife.Rect(xmin, ymin, xmax - xmin, ymax - ymin)
			else:
				area = fife.ScreenPoint(xx, yy)
			instances = self.session.view.cam.getMatchingInstances(
				area,
				self.session.view.layers[LAYERS.OBJECTS],
				False) # False for accurate

			# get selection components
			instances = (self.fife_instance_to_uh_instance(i) for i in instances)
			instances = [i for i in instances if i is not None]

			# We only consider selectable items when dragging a selection box.
			instances = self.filter_selectable(instances)

			# If there is at least one player unit, we don't select any enemies.
			# This applies to both buildings and ships.
			if any((self.is_owned_by_player(instance) for instance in instances)):
				instances = self.filter_owner(instances)

			self._update_selection(instances, do_multi)

		elif evt.getButton() == fife.MouseEvent.RIGHT:
			pass
		else:
			super().mouseDragged(evt)
			return
		evt.consume()

	def mouseReleased(self, evt):
		if evt.getButton() == fife.MouseEvent.LEFT and hasattr(self, 'select_begin'):
			self.apply_select()
			del self.select_begin, self.select_old
			self.session.view.renderer['GenericRenderer'].removeAll(self.__class__._SELECTION_RECTANGLE_NAME)
		elif evt.getButton() == fife.MouseEvent.RIGHT:
			pass
		else:
			super().mouseReleased(evt)
			return
		evt.consume()

	def apply_select(self):
		"""
		Called when selected instances changes. (Shows their menu)
		Does not do anything when nothing is selected, i.e. doesn't hide their menu.
		If one of the selected instances can attack, switch mousetool to AttackingTool.
		"""
		if self.session.world.health_visible_for_all_health_instances:
			self.session.world.toggle_health_for_all_health_instances()
		selected = self.session.selected_instances
		if not selected:
			return
		if len(selected) == 1:
			next(iter(selected)).get_component(SelectableComponent).show_menu()
		else:
			self.session.ingame_gui.show_multi_select_tab(selected)

		# local import to prevent cycle
		from horizons.gui.mousetools.attackingtool import AttackingTool
		# change session cursor to attacking tool if selected instances can attack
		found_military = any(hasattr(i, 'attack') and i.owner.is_local_player
		                     for i in selected)
		# Handover to AttackingTool without deselecting
		self.deselect_at_end = not found_military

		if found_military and not isinstance(self.session.ingame_gui.cursor, AttackingTool):
			self.session.ingame_gui.set_cursor('attacking')
		if not found_military and isinstance(self.session.ingame_gui.cursor, AttackingTool):
			self.session.ingame_gui.set_cursor('default')

	def mousePressed(self, evt):
		if evt.isConsumedByWidgets():
			super().mousePressed(evt)
			return
		elif evt.getButton() == fife.MouseEvent.LEFT:
			if self.session.selected_instances is None:
				# this is a very odd corner case, it should only happen after the session has been ended
				# we can't allow to just let it crash however
				self.log.error('Error: selected_instances is None. Please report this!')
				traceback.print_stack()
				self.log.error('Error: selected_instances is None. Please report this!')
				return
			instances = self.get_hover_instances(evt)
			self.select_old = frozenset(self.session.selected_instances) if evt.isControlPressed() else frozenset()

			instances = list(filter(self.is_selectable, instances))
			# On single click, only one building should be selected from the hover_instances.
			# The if is for [] and [single_item] cases (they crashed).
			# It acts as user would expect: instances[0] selects buildings in front first.
			instances = instances if len(instances) <= 1 else [instances[0]]

			self._update_selection(instances)

			self.select_begin = (evt.getX(), evt.getY())
			self.session.ingame_gui.hide_menu()
		elif evt.getButton() == fife.MouseEvent.RIGHT:
			target_mapcoord = self.get_exact_world_location(evt)
			for i in self.session.selected_instances:
				if i.movable:
					Act(i, target_mapcoord.x, target_mapcoord.y).execute(self.session)
		else:
			super().mousePressed(evt)
			return
		evt.consume()

	def _draw_rect_line(self, start, end):
		renderer = self.session.view.renderer['GenericRenderer']
		renderer.addLine(self.__class__._SELECTION_RECTANGLE_NAME,
		                 fife.RendererNode(start), fife.RendererNode(end),
		                 200, 200, 200)

	def _update_selection(self, instances, do_multi=False):
		"""
		self.select_old are old instances still relevant now (esp. on ctrl)
		@param instances: uh instances
		@param do_multi: True if selection rectangle on drag is used
		"""
		self.log.debug("update selection %s", [str(i) for i in instances])

		if do_multi: # add to selection
			instances = self.select_old.union(instances)
		else: # this is for deselecting among a selection with ctrl
			instances = self.select_old.symmetric_difference(instances)

		# sanity:
		# - no multiple entities from enemy selected
		if len(instances) > 1:
			user_instances = self.filter_owner(instances)
			if user_instances: # check at least one remaining
				instances = user_instances
			else:
				instances = [next(iter(instances))]
		selectable = frozenset(self.filter_component(SelectableComponent, instances))

		# apply changes
		selected_components = set(self.filter_component(SelectableComponent,
		                          self.filter_selectable(self.session.selected_instances)))
		for sel_comp in selected_components - selectable:
			sel_comp.deselect()
		for sel_comp in selectable - selected_components:
			sel_comp.select()

		self.session.selected_instances = {i.instance for i in selectable}
