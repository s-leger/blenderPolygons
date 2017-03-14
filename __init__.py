﻿# -*- coding:utf-8 -*-

#  ***** GPL LICENSE BLOCK *****
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#  All rights reserved.
#  ***** GPL LICENSE BLOCK *****

bl_info = {
    'name': 'PolyLib',
    'description': 'Polygons detection from unordered splines',
    'author': 's-leger',
    'license': 'GPL',
    'deps': 'shapely',
    'version': (1, 1),
    'blender': (2, 7, 8),
    'location': 'View3D > Tools > Polygons',
    'warning': '',
    'wiki_url': 'https://github.com/s-leger/blenderPolygons/wiki',
    'tracker_url': 'https://github.com/s-leger/blenderPolygons/issues',
    'link': 'https://github.com/s-leger/blenderPolygons',
    'support': 'COMMUNITY',
    'category': '3D View'
    }
    
import os
import sys
import time
import bpy
import bgl
import blf
import numpy as np
from math import cos, sin, pi, atan2
import bmesh

# let shapely import throw ImportError when missing
from shapely.geometry import (
    LineString, Polygon, GeometryCollection, box
)
import shapely.ops
import shapely.prepared

try:
    import shapely.speedups
    if shapely.speedups.available:
        shapely.speedups.enable()
except:
    pass
    
from .bitarray import BitArray
from .pyqtree import _QuadTree
from mathutils import Vector, Matrix
from mathutils.geometry import intersect_line_plane, interpolate_bezier
from bpy_extras import view3d_utils
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import StringProperty, FloatProperty, PointerProperty, EnumProperty, IntProperty, BoolProperty
from bpy.utils import previews as iconsLib
from bpy.app.handlers import persistent

# module globals vars dict
vars_dict = {
    # spacial tree for segments and points
    'seg_tree': None, 
    'point_tree': None, 
    # keep track of shapely geometry selection sets
    'select_polygons': None, 
    'select_lines': None,
    'select_points':None
    }
    
icons_dict = {}

# module constants 
# precision 1e-4 = 0.1mm
EPSILON = 1.0e-4
# Qtree params
MAX_ITEMS = 10
MAX_DEPTH = 20

# http://blenderscripting.blogspot.ch/2011/07/bgl-drawing-with-opengl-onto-blender-25.html
class GlDrawOnScreen():
    explanation_colour = (1.0, 1.0, 1.0, 0.7)
    line_colour = (1.0, 1.0, 1.0, 0.5)
    selected_colour = (1.0, 1.0, 0.0, 0.5)
    def String(self, text, x, y, size, colour):
        ''' my_string : the text we want to print
            pos_x, pos_y : coordinates in integer values
            size : font height.
            colour : used for definining the colour'''
        dpi, font_id = 72, 0 # dirty fast assignment
        bgl.glColor4f(*colour)
        blf.position(font_id, x, y, 0)
        blf.size(font_id, size, dpi)
        blf.draw(font_id, text)
    def _end(self):
        bgl.glEnd()
        bgl.glPopAttrib()
        bgl.glLineWidth(1)
        bgl.glDisable(bgl.GL_BLEND)
        bgl.glColor4f(0.0, 0.0, 0.0, 1.0)
    def _start_line(self, colour, width=2, style=bgl.GL_LINE_STIPPLE):
        bgl.glPushAttrib(bgl.GL_ENABLE_BIT)
        bgl.glLineStipple(1, 0x9999)
        bgl.glEnable(style)
        bgl.glEnable(bgl.GL_BLEND)
        bgl.glColor4f(*colour)
        bgl.glLineWidth(width)
        bgl.glBegin(bgl.GL_LINE_STRIP)
    def Line(self, x0, y0, x1, y1, colour, width=2):
        self._start_line(colour, width) 
        bgl.glVertex2i(x0, y0)
        bgl.glVertex2i(x1, y1)
        self._end()
    def Rectangle(self, x0, y0, x1, y1, colour, width=2):
        self._start_line(colour, width) 
        bgl.glVertex2i(x0, y0)
        bgl.glVertex2i(x1, y0)
        bgl.glVertex2i(x1, y1)
        bgl.glVertex2i(x0, y1)
        bgl.glVertex2i(x0, y0)
        self._end()
    def PolyLine(self, pts, colour, width=2):
        self._start_line(colour, width) 
        for pt in pts:
            x, y = pt
            bgl.glVertex2i(x, y)
        self._end()
    def Polygon(self, pts, colour, width=2, style=bgl.GL_LINE):
        self._start_line(colour, width, style) 
        #bgl.glPushAttrib(bgl.GL_ENABLE_BIT)
        #bgl.glEnable(bgl.GL_BLEND)
        #bgl.glColor4f(*colour)    
        #bgl.glBegin(bgl.GL_POLYGON)
        for pt in pts:
            x, y = pt
            bgl.glVertex2f(x, y)  
        self._end()

class CoordSys(object):
    """
        reference coordsys 
        world : matrix from local to world
        invert: matrix from world to local
        width, height: bonding region size
    """
    def __init__(self, objs):
        x = []
        y = []
        if len(objs) > 0:
            if hasattr(objs[0], 'bound_box'):
                for obj in objs:
                    pos = obj.location
                    x.append(obj.bound_box[0][0] + pos.x)
                    x.append(obj.bound_box[6][0] + pos.x)
                    y.append(obj.bound_box[0][1] + pos.y)
                    y.append(obj.bound_box[6][1] + pos.y) 
            elif hasattr(objs[0], 'bounds'):
                for geom in objs:
                    x0, y0, x1, y1 = geom.bounds
                    x.append(x0)
                    x.append(x1)
                    y.append(y0)
                    y.append(y1)
            else:
                raise Exception("CoordSys require at least one object with bounds or bound_box property to initialize")
        else:
            raise Exception("CoordSys require at least one object to initialize bounds")
        x0 = min(x)
        y0 = min(y)
        x1 = max(x)
        y1 = max(y)
        width, height = x1-x0, y1-y0
        midx, midy = x0+width/2.0, y0+height/2.0
        # reference coordsys bounding box center
        self.world = Matrix([
            [1,0,0,midx],
            [0,1,0,midy],
            [0,0,1,0],
            [0,0,0,1],
            ])
        self.invert = self.world.inverted()
        self.width  = width
        self.height = height
        
class Prolongement():
    """ intersection of two segments outside segment (projection)
        c0 = extremite sur le segment courant
        c1 = intersection point on oposite segment
        id = oposite segment id
        t = param t on oposite segment 
        d = distance from ends to segment
        insert = do we need to insert the point on other segment
        use id, c1 and t to insert segment slices
    """ 
    def __init__(self, c0, c1, id, t, d):
        self.length = c0.distance(c1)
        self.c0 = c0
        self.c1 = c1
        self.id = id
        self.t = t
        self.d = d
        
class Point():
    def __init__(self, co, precision=EPSILON):
        self.users = 0
        self.co = tuple(co)
        x,y,z = co
        self.shapeIds = []
        self.bounds = (x-precision, y-precision, x+precision, y+precision)
    @property
    def geom(self):
        return shapely.geometry.Point(self.co)
    def vect(self, point):
        """ vector from this point to another """    
        return np.subtract(point.co, self.co)
    def distance(self, point):
        """ euclidian distance between points """    
        return np.linalg.norm(self.vect(point))
    def add_user(self):
        self.users += 1
        
class Segment():
    
    def __init__(self, c0, c1, extend=EPSILON):
        
        self.c0 = c0
        self.c1 = c1
        self._splits = []
        
        self.available = True
        # ensure uniqueness when merge 
        
        self.opposite = False
        # this seg has an opposite
        
        self.original = False
        # source of opposite
        
        x0, y0, z0  = c0.co
        x1, y1, z1  = c1.co
        self.bounds = (min(x0, x1)-extend, min(y0, y1)-extend, max(x0, x1)+extend, max(y0, y1)+extend)
    
    @property
    def splits(self):
        return sorted(self._splits)
    
    @property
    def vect(self):
        """ vector c0-c1"""
        return np.subtract(self.c1.co, self.c0.co)
        
    @property
    def vect_2d(self):
        v = self.vect
        v[2] = 0
        return v
    
    def lerp(self, t):
        return np.add(self.c0.co, np.multiply(t, self.vect))
    
    def _point_sur_segment(self, point):
        """ _point_sur_segment 
            point: Point
            t: param t de l'intersection sur le segment courant
            d: distance laterale perpendiculaire
        """
        vect = self.vect
        dp = point.vect(self.c0)
        dl = np.linalg.norm(vect)
        d = np.linalg.norm(np.cross(vect, dp))/dl
        t = -np.divide(np.dot(dp,vect),np.multiply(dl,dl))
        if d < EPSILON:
            if t > 0 and t < 1:
                self._append_splits((t, point))
        
    def is_end(self, point):
        return point == self.c0 or point == self.c1
          
    def min_intersect_dist(self, t, point):
        """ distance intersection extremite la plus proche
            t: param t de l'intersection sur le segment courant
            point: Point d'intersection
            return d: distance
        """
        if t > 0.5:
            return self.c1.distance(point)
        else:
            return self.c0.distance(point)  
            
    def intersect(self, segment):
        """ point_sur_segment return 
            p: point d'intersection
            u: param t de l'intersection sur le segment courant
            v: param t de l'intersection sur le segment segment
        """
        v2d = self.vect_2d
        c2 = np.cross(segment.vect_2d, (0,0,1)) 
        d  = np.dot(v2d, c2)
        if d == 0:
            # segments paralleles
            segment._point_sur_segment(self.c0)
            segment._point_sur_segment(self.c1)
            self._point_sur_segment(segment.c0)
            self._point_sur_segment(segment.c1)
            return False, 0, 0, 0
        c1 = np.cross(v2d, (0,0,1))
        v3 = self.c0.vect(segment.c0)
        v3[2] = 0.0
        u = np.dot(c2,v3)/d
        v = np.dot(c1,v3)/d
        co = self.lerp(u)
        #print("pt:%s u:%f v:%f" % (co, u, v))
        return True, co, u, v
        
    def _append_splits(self, split):
        """
            append a unique split point 
        """
        if split not in self._splits:
            self._splits.append(split)
            
    def slice(self, d, t, point):
        if d > EPSILON:
            if t > 0.5:
                if point != self.c1:
                    self._append_splits((t, point))
            else:
                if point != self.c0:
                    self._append_splits((t,point))
                    
    def add_user(self):
        self.c0.add_user()
        self.c1.add_user()
        
    def consume(self):
        self.available = False

class Shape():
    """    
        Ensure uniqueness and fix precision issues by design
        implicit closed with last point 
        require point_tree and seg_tree
    """
    
    def __init__(self, points=[]):
        """
            @vertex: list of coords
        """
        self.available = True
        # Ensure uniqueness of shape when merging
    
        self._segs = []
        # Shape segments
    
        self.shapeId = []
        # Id of shape in shapes to keep a track of shape parts when merging
    
        self._create_segments(points)
        
    def _create_segments(self, points):
        global vars_dict
        if vars_dict['seg_tree'] is None:
            raise RuntimeError('Shape._create_segments() require a global seg_tree spacial index ')
        # skip null segments with unique points test
        self._segs = list(vars_dict['seg_tree'].newSegment(points[v], points[v+1]) for v in range(len(points)-1) if points[v] != points[v+1])
    
    @property
    def coords(self):
        coords = list(seg.c0.co for seg in self._segs)
        coords.append(self.c1.co)
        return coords
        
    @property
    def points(self):
        points = list(seg.c0 for seg in self._segs)
        points.append(self.c1)
        return points
    
    @property
    def c0(self):
        if not self.valid:
            raise RuntimeError('Shape does not contains any segments')
        return self._segs[0].c0
    
    @property
    def c1(self):
        if not self.valid:
            raise RuntimeError('Shape does not contains any segments')
        return self._segs[-1].c1
    
    @property
    def nbsegs(self):
        return len(self._segs)
    
    @property
    def valid(self):
        return self.nbsegs > 0
        
    @property
    def closed(self):
        return self.valid and bool(self.c0 == self.c1)
    
    def merge(self, shape):
        """ merge this shape with specified shape
            shapes must share at least one vertex
        """
        if not self.valid or not shape.valid:
            raise RuntimeError('Trying to merge invalid shape')
        if self.c1 == shape.c1 or self.c0 == shape.c0:
            shape._reverse()
        if self.c1 == shape.c0:
            self._segs += shape._segs
        elif shape.c1 == self.c0:
            self._segs = shape._segs + self._segs
        else:
            # should never happen
            raise RuntimeError("Shape merge failed {} {} {} {}".format(id(self), id(shape), self.shapeId, shape.shapeId))
    
    def _reverse(self):
        """
            reverse vertex order
        """
        points = self.points[::-1]
        self._create_segments(points)
                       
    def slice(self, shapes):
        """
            slice shape into smaller parts at intersections
        """
        if not self.valid:
            raise RuntimeError('Cant slice invalid shape')
        points = []
        for seg in self._segs:
            if seg.available and not seg.original:
                seg.consume()
                points.append(seg.c0)
                if seg.c1.users > 2:
                    points.append(seg.c1)
                    shape = Shape(points)
                    shapes.append(shape)
                    points = []
        if len(points) > 0:
            points.append(self.c1)
            shape = Shape(points)
            shapes.append(shape)    
           
    def add_points(self):
        """
            add points from intersection data
        """
        points = []
        if self.nbsegs > 0:
            for seg in self._segs:
                points.append(seg.c0)
                for split in seg.splits:
                    points.append(split[1])
            points.append(self.c1)
        self._create_segments(points)
        
    def set_users(self):
        """
            add users on segments and points
        """
        for seg in self._segs:
            seg.add_user()
                   
    def consume(self):
        self.available = False
         
class Qtree(_QuadTree):
    """
        The top spatial index to be created by the user. Once created it can be
        populated with geographically placed members that can later be tested for
        intersection with a user inputted geographic bounding box. 
    """
    def __init__(self, coordsys, extend=EPSILON, max_items=MAX_ITEMS, max_depth=MAX_DEPTH):
        """
            objs may be blender objects or shapely geoms
            extend: how much seek arround
        """
        self._extend = extend
        self._geoms = []
        
        # store input coordsys
        self.coordsys = coordsys
        
        super(Qtree, self).__init__(0, 0, coordsys.width, coordsys.height, max_items, max_depth)
      
    @property
    def ngeoms(self):
        return len(self._geoms)
     
    def build(self, geoms):
        """
            Build a spacial index from shapely geoms
        """
        t = time.time()
        self._geoms = geoms
        for i, geom in enumerate(geoms):
            self._insert(i, geom.bounds)
        print("Qtree.build() :%.2f seconds" % (time.time()-t))
        
    def insert(self, id, geom):
        self._geoms.append(geom)
        self._insert(id, geom.bounds)
    
    def newPoint(self, co):
        point = Point(co, self._extend)
        count, found  = self.intersects(point)
        for id in found:
            return self._geoms[id]
        self.insert(self.ngeoms, point)
        return point
    
    def newSegment(self, c0, c1):
        """
            allow "opposite" segments,
            those segments are not found by intersects
            and not stored in self.geoms
        """
        new_seg = Segment(c0, c1, self._extend)
        count, found  = self.intersects(new_seg)
        for id in found:
            old_seg = self._geoms[id]
            if (old_seg.c0 == c0 and old_seg.c1 == c1):
                return old_seg
            if (old_seg.c0 == c1 and old_seg.c1 == c0):
                if not old_seg.opposite:
                    old_seg.opposite = new_seg
                    new_seg.original = old_seg
                return old_seg.opposite
        self.insert(self.ngeoms, new_seg)
        return new_seg
        
    def intersects(self, geom):
        selection = list(self._intersect(geom.bounds))
        count = len(selection)
        return count, sorted(selection)
       
class Io():
    
    @staticmethod
    def build_default_mat(name, color=(1.0,1.0,1.0)):
        midx = bpy.data.materials.find(name)   
        if midx < 0:
            mat = bpy.data.materials.new(name)
            mat.diffuse_color = color
        else:
            mat = bpy.data.materials[midx]
        return mat
        
    @staticmethod
    def ensure_iterable(obj):
        try:
            iterator = iter(obj)
        except TypeError:
            obj = [obj]
        return obj
    
    # Conversion methods
    @staticmethod
    def _to_geom(shape):
        if not shape.valid:
            raise RuntimeError('Cant convert invalid shape to Shapely LineString')
        return shapely.geometry.LineString(shape.coords)
        
    @staticmethod
    def shapes_to_geoms(shapes):
        return [Io._to_geom(shape) for shape in shapes]
    
    @staticmethod
    def _to_shape(geometry, shapes):
        global vars_dict
        if vars_dict['point_tree'] is None:
            raise RuntimeError("geoms to shapes require a global point_tree spacial index")
        if  hasattr(geometry, 'exterior'):
            Io._to_shape(geometry.exterior, shapes)
            for geom in geometry.interiors:
                Io._to_shape(geom, shapes)
        elif hasattr(geometry, 'geoms'):
            # Multi and Collections
            for geom in geometry.geoms:
                Io._to_shape(geom, shapes)
        else:
            points =  list(vars_dict['point_tree'].newPoint(p) for p in list(geometry.coords))
            shape = Shape(points)
            shapes.append(shape)
        
    @staticmethod    
    def geoms_to_shapes(geoms, shapes=[]):
        for geom in geoms:
            Io._to_shape(geom, shapes)
        return shapes
    
    # Input methods
    @staticmethod
    def _interpolate_bezier(pts, wM, p0, p1, resolution):
        # straight segment, worth testing here 
        # since this can lower points count by a resolution factor 
        # use normalized to handle non linear t
        if resolution == 0:
            pts.append(wM * p0.co.to_3d())
        else:
            v  = (p1.co-p0.co).normalized()
            d1 = (p0.handle_right-p0.co).normalized()
            d2 = (p1.co-p1.handle_left).normalized()
            if d1 == v and d2 == v:
                pts.append(wM * p0.co.to_3d())
            else:    
                seg = interpolate_bezier(wM * p0.co, 
                    wM * p0.handle_right,
                    wM * p1.handle_left,
                    wM * p1.co, 
                    resolution)
                for i in range(resolution-1):
                    pts.append(seg[i].to_3d())
            
    @staticmethod
    def _coords_from_spline(wM, resolution, spline):
        pts = []
        if spline.type == 'POLY':
            pts = [wM * p.co.to_3d() for p in spline.points]
            if spline.use_cyclic_u:
                pts.append(pts[0])     
        elif spline.type == 'BEZIER':
            points = spline.bezier_points
            for i in range(1, len(points)):
                p0 = points[i-1]
                p1 = points[i]
                Io._interpolate_bezier(pts, wM, p0, p1, resolution)
            pts.append(wM * points[-1].co)
            if spline.use_cyclic_u:
                p0 = points[-1]
                p1 = points[0]
                Io._interpolate_bezier(pts, wM, p0, p1, resolution)
                pts.append(pts[0])
        return pts
    
    @staticmethod
    def _add_geom_from_curve(curve, invert_world, resolution, geoms):
        wM = invert_world * curve.matrix_world 
        for spline in curve.data.splines:
            pts = Io._coords_from_spline(wM, resolution, spline)
            geom = shapely.geometry.LineString(pts)
            geoms.append(geom)
    
    @staticmethod
    def curves_to_geoms(curves, resolution, geoms=[]):
        """ 
            @curves : blender curves collection 
            Return coordsys for outputs 
        """
        curves = Io.ensure_iterable(curves)
        coordsys = CoordSys(curves)
        t = time.time()
        for curve in curves:
            Io._add_geom_from_curve(curve, coordsys.invert, resolution, geoms)
        print("Io.curves_as_line() :%.2f seconds" % (time.time()-t))
        return coordsys
        
    @staticmethod
    def _add_shape_from_curve(curve, invert_world, resolution, shapes):
        global vars_dict
        wM = invert_world * curve.matrix_world 
        for spline in curve.data.splines:
            pts = Io._coords_from_spline(wM, resolution, spline)
            pts = [vars_dict['point_tree'].newPoint(pt) for pt in pts]
            shape = Shape(points=pts)
            shapes.append(shape)
    
    @staticmethod
    def curves_to_shapes(curves, coordsys, resolution, shapes=[]):
        """ 
            @curves : blender curves collection 
            Return simple shapes 
        """
        curves = Io.ensure_iterable(curves)
        t = time.time()
        for curve in curves:
            Io._add_shape_from_curve(curve, coordsys.invert, resolution, shapes)
        print("Io.curves_to_shapes() :%.2f seconds" % (time.time()-t))
        
    # Output methods
    @staticmethod 
    def _poly_to_wall(scene, matrix_world, poly, height, name):
        global vars_dict
        curve = bpy.data.curves.new(name, type = 'CURVE')
        curve.dimensions = "2D"
        curve.fill_mode = 'BOTH'
        curve.extrude = height
        n_ext = len(poly.exterior.coords)
        n_int = len(poly.interiors)
        Io._add_spline(curve, poly.exterior)
        for geom in poly.interiors:
            Io._add_spline(curve, geom)
        curve_obj = bpy.data.objects.new(name, curve) 
        curve_obj.matrix_world = matrix_world
        scene.objects.link(curve_obj)
        curve_obj.select = True
        return n_ext, n_int, curve_obj
    
    @staticmethod 
    def wall_uv(me, bm):
        
        for face in bm.faces:
            face.select = face.material_index > 0
        
        bmesh.update_edit_mesh(me, True)
        bpy.ops.uv.cube_project(scale_to_bounds=False, correct_aspect=True)

        for face in bm.faces:
            face.select = face.material_index < 1
            
        bmesh.update_edit_mesh(me, True)
        bpy.ops.uv.smart_project(use_aspect=True, stretch_to_bounds=False)
        
    @staticmethod 
    def to_wall(scene, coordsys, geoms, height, name, walls = []):
        """
            use curve extrude as it does respect vertices number and is not removing doubles
            so it is easy to set material index
            cap faces are tri, sides faces are quads
        """
        geoms = Io.ensure_iterable(geoms)
        int_mat = Io.build_default_mat('inside', (0.5,1.0,1.0))
        out_mat = Io.build_default_mat('outside', (0.5,1.0,0.5))
        oth_mat = Io.build_default_mat('cuts', (1.0,0.2,0.2))
        for poly in geoms:
            if hasattr(poly, 'exterior'):
                half_height = height/2.0
                n_ext, n_int, obj = Io._poly_to_wall(scene, coordsys.world, poly, half_height, name)
                scene.objects.active = obj
                bpy.ops.object.convert(target="MESH")
                bpy.ops.object.mode_set(mode='EDIT')
                me = obj.data
                bm = bmesh.from_edit_mesh(me)
                bm.verts.ensure_lookup_table()
                bm.faces.ensure_lookup_table()
                for v in bm.verts:
                    v.co.z += half_height
                nfaces = 0
                for i, f in enumerate(bm.faces):
                    if len(f.verts) > 3:
                        nfaces = i
                        break
                # walls without holes are inside        
                mat_index = 1 if n_int > 0 else 2
                for i in range(nfaces, nfaces+n_ext-1):
                    bm.faces[i].material_index = mat_index
                for i in range(nfaces+n_ext-1, len(bm.faces)):
                    bm.faces[i].material_index = 2
                bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.003)
                bmesh.update_edit_mesh(me, True)
                Io.wall_uv(me, bm)
                bpy.ops.object.mode_set(mode='OBJECT')
                bpy.ops.object.shade_flat()
                me.materials.append(oth_mat)
                me.materials.append(out_mat)
                me.materials.append(int_mat)
                walls.append(obj)
        return walls
        
    @staticmethod
    def _add_spline(curve, geometry):
        coords = list(geometry.coords)
        spline = curve.splines.new('POLY')
        spline.use_endpoint_u = False
        spline.use_cyclic_u  = coords[0] == coords[-1]
        spline.points.add(len(coords)-1)
        for i, coord in enumerate(coords):
            x,y,z = coord
            spline.points[i].co = (x,y,z,1)
    
    @staticmethod
    def _as_spline(curve, geometry):
        """
            add a spline into a blender curve
            @curve : blender curve
        """
        if hasattr(geometry, 'exterior'):
            # Polygon
            Io._add_spline(curve, geometry.exterior)
            for geom in geometry.interiors:
                Io._add_spline(curve, geom)
        elif hasattr(geometry, 'geoms'):
            # Multi and Collections
            for geom in geometry.geoms:
                Io._as_spline(curve, geom)
        else:
            # LinearRing, LineString and Shape
            Io._add_spline(curve, geometry)
  
    @staticmethod
    def to_curve(scene, coordsys, geoms, name, dimensions = '3D'):
        global vars_dict
        t = time.time()
        geoms = Io.ensure_iterable(geoms)
        curve = bpy.data.curves.new(name, type = 'CURVE')
        curve.dimensions = dimensions
        for geom in geoms:
            Io._as_spline(curve, geom)
        curve_obj = bpy.data.objects.new(name, curve)
        curve_obj.matrix_world = coordsys.world
        scene.objects.link(curve_obj)
        curve_obj.select = True
        print("Io.to_curves() :%.2f seconds" % (time.time()-t))
        return curve_obj
    
    @staticmethod
    def to_curves(scene, coordsys, geoms, name, dimensions = '3D'):
        geoms = Io.ensure_iterable(geoms)
        return [Io.to_curve(scene, coordsys, geom, name, dimensions) for geom in geoms]
         
class ShapelyOps():
     
    @staticmethod
    def min_bounding_rect(geom):
        """ min_bounding_rect
            minimum area oriented bounding rect 
        """
        # Compute edges (x2-x1,y2-y1)
        if geom.convex_hull.geom_type == 'Polygon':
            hull_points_2d = [list(coord[0:2]) for coord in list(geom.convex_hull.exterior.coords)]
        else:
            hull_points_2d = [list(coord[0:2]) for coord in list(geom.convex_hull.coords)]
        edges = np.zeros( (len(hull_points_2d)-1,2) ) # empty 2 column array
        for i in range( len(edges) ):
            edge_x = hull_points_2d[i+1][0] - hull_points_2d[i][0]
            edge_y = hull_points_2d[i+1][1] - hull_points_2d[i][1]
            edges[i] = [edge_x,edge_y]
        # Calculate edge angles   atan2(y/x)
        edge_angles = np.zeros( (len(edges)) ) # empty 1 column array
        for i in range( len(edge_angles) ):
            edge_angles[i] = atan2( edges[i,1], edges[i,0] )
        # Check for angles in 1st quadrant
        for i in range( len(edge_angles) ):
            edge_angles[i] = abs( edge_angles[i] % (pi/2) ) # want strictly positive answers
        # Remove duplicate angles
        edge_angles = np.unique(edge_angles)
        # Test each angle to find bounding box with smallest area
        min_bbox = (0, sys.maxsize, 0, 0, 0, 0, 0, 0) # rot_angle, area, width, height, min_x, max_x, min_y, max_y
        #print "Testing", len(edge_angles), "possible rotations for bounding box... \n"
        for i in range( len(edge_angles) ):
            # Create rotation matrix to shift points to baseline
            # R = [ cos(theta)      , cos(theta-PI/2)
            #       cos(theta+PI/2) , cos(theta)     ]
            R = np.array([ [ cos(edge_angles[i]), cos(edge_angles[i]-(pi/2)) ], [ cos(edge_angles[i]+(pi/2)), cos(edge_angles[i]) ] ])
            # Apply this rotation to convex hull points
            rot_points = np.dot(R, np.transpose(hull_points_2d) ) # 2x2 * 2xn
            # Find min/max x,y points
            min_x = np.nanmin(rot_points[0], axis=0)
            max_x = np.nanmax(rot_points[0], axis=0)
            min_y = np.nanmin(rot_points[1], axis=0)
            max_y = np.nanmax(rot_points[1], axis=0)
            # Calculate height/width/area of this bounding rectangle
            width = max_x - min_x
            height = max_y - min_y
            area = width*height
            # Store the smallest rect found first (a simple convex hull might have 2 answers with same area)
            if (area < min_bbox[1]):
                min_bbox = ( edge_angles[i], area, width, height, min_x, max_x, min_y, max_y )
        # Re-create rotation matrix for smallest rect
        angle = min_bbox[0]   
        R = np.array([ [ cos(angle), cos(angle-(pi/2)) ], [ cos(angle+(pi/2)), cos(angle) ] ])
        # min/max x,y points are against baseline
        min_x = min_bbox[4]
        max_x = min_bbox[5]
        min_y = min_bbox[6]
        max_y = min_bbox[7]
        # Calculate center point and project onto rotated frame
        center_x = (min_x + max_x)/2
        center_y = (min_y + max_y)/2
        center_point = np.dot( [ center_x, center_y ], R )
        if min_bbox[2] > min_bbox[3]:
            a = -cos(angle)
            b = sin(angle)
            w = min_bbox[2]
            h = min_bbox[3]
        else:
            a = -cos(angle+(pi/2))
            b = sin(angle+(pi/2))
            w = min_bbox[3]
            h = min_bbox[2]
        tM = Matrix([[a, b, 0, center_point[0]], [-b, a, 0, center_point[1]], [0,0,1,0], [0,0,0,1]])
        return tM, w, h
        
    @staticmethod
    def detect_polygons(geoms):
        """ detect_polygons
        """ 
        print("Ops.detect_polygons()")
        t = time.time()
        result, dangles, cuts, invalids = shapely.ops.polygonize_full(geoms)
        print("Ops.detect_polygons() :%.2f seconds" % (time.time()-t))
        return result, dangles, cuts, invalids
        
    @staticmethod
    def optimize(geoms, tolerance=0.001, preserve_topology=True):
        """ optimize 
        """ 
        t = time.time()
        geoms = Io.ensure_iterable(geoms)
        optimized = [geom.simplify(tolerance, preserve_topology) for geom in geoms]
        print("Ops.optimize() :%.2f seconds" % (time.time()-t))
        return optimized
        
    @staticmethod
    def union(geoms):
        """ union (shapely based)
            cascaded union - may require snap before use to fix precision issues
            use union2 for best performances
        """         
        t = time.time()
        geoms = Io.ensure_iterable(geoms)
        collection = shapely.geometry.GeometryCollection(geoms)
        union = shapely.ops.cascaded_union(collection)
        print("Ops.union() :%.2f seconds" % (time.time()-t))
        return union
  
class ShapeOps():
    
    @staticmethod
    def union(shapes, extend=0.001):
        """ union2 (Shape based) 
            cascaded union
            require point_tree and seg_tree
        """        
        split = ShapeOps.split(shapes, extend=extend)
        union = ShapeOps.merge(split)
        return union
        
    @staticmethod    
    def _intersection_point(d, t, point, seg):
        if d > EPSILON:
            return point
        elif t > 0.5:
            return seg.c1
        else:
            return seg.c0
    
    @staticmethod        
    def split(shapes, extend=0.01):
        """ _split 
            detect intersections between segments and slice shapes according
            is able to project segment ends on closest segment
            require point_tree and seg_tree
        """
        global vars_dict
        t = time.time()
        new_shapes = []
        segs = vars_dict['seg_tree']._geoms
        nbsegs = len(segs)
        it_start = [None for x in range(nbsegs)] 
        it_end   = [None for x in range(nbsegs)]
        for s, seg in enumerate(segs):
            count, idx = vars_dict['seg_tree'].intersects(seg)
            for id in idx:
                if id > s:
                    intersect, co, u, v = seg.intersect(segs[id])
                    if intersect:                    
                        point = vars_dict['point_tree'].newPoint(co)
                        du = seg.min_intersect_dist(u, point)
                        dv = segs[id].min_intersect_dist(v, point)
                        # point intersection sur segment id
                        pt = ShapeOps._intersection_point(dv, v, point, segs[id])
                        # print("s:%s id:%s u:%7f v:%7f du:%7f dv:%7f" % (s, id, u, v, du, dv))
                        if u <= 0:
                            # prolonge segment s c0
                            if du < extend and not seg.is_end(pt):
                                it = Prolongement(seg.c0, pt, id, v, du)
                                last = it_start[s]
                                if last is None or last.length > it.length:
                                    it_start[s] = it 
                        elif u < 1:
                            # intersection sur segment s
                            seg.slice(du, u, pt)
                        else:
                            # prolonge segment s c1
                            if du < extend and not seg.is_end(pt):
                                it = Prolongement(seg.c1, pt, id, v, du)
                                last = it_end[s]
                                if last is None or last.length > it.length:
                                    it_end[s] = it
                        pt = ShapeOps._intersection_point(du, u, point, seg)
                        if v <= 0:
                            # prolonge segment id c0
                            if dv < extend and not segs[id].is_end(pt):
                                it = Prolongement(segs[id].c0, pt, s, u, dv)
                                last = it_start[id]
                                if last is None or last.length > it.length:
                                    it_start[id] = it 
                        elif v < 1:
                            # intersection sur segment s
                            segs[id].slice(dv, v, pt)
                        else:
                            # prolonge segment s c1
                            if dv < extend and not segs[id].is_end(pt):
                                it = Prolongement(segs[id].c1, pt, s, u, dv)
                                last = it_end[id]
                                if last is None or last.length > it.length:
                                    it_end[id] = it
        for it in it_start:
            if it is not None:
                #print("it_start[%s] id:%s t:%4f d:%4f" % (s, it.id, it.t, it.d) )
                if it.t > 0 and it.t < 1:
                    segs[it.id]._append_splits((it.t, it.c1))
                if it.d > EPSILON:
                    shape = Shape([it.c0, it.c1])
                    shapes.append(shape)
        for it in it_end:            
            if it is not None:
                #print("it_end[%s] id:%s t:%4f d:%4f" % (s, it.id, it.t, it.d) )
                if it.t > 0 and it.t < 1:
                    segs[it.id]._append_splits((it.t, it.c1))
                if it.d > EPSILON:
                    shape = Shape([it.c0, it.c1])
                    shapes.append(shape)
        print("Ops.split() intersect :%.2f seconds" % (time.time()-t))
        t = time.time()
        for shape in shapes:
            shape.add_points() 
        for shape in shapes:
            shape.set_users() 
        for shape in shapes:
            if shape.valid:
                shape.slice(new_shapes)
        print("Ops.split() slice :%.2f seconds" % (time.time()-t))
        return new_shapes
    
    @staticmethod
    def merge(shapes):
        """ merge
            merge shapes ends 
            reverse use seg_tree
            does not need tree as all:
            - set shape ids to end vertices
            - traverse shapes looking for points with 2 shape ids
            - merge different shapes according
        """
        t = time.time()
        merged = []
        nbshapes = len(shapes)
        for i, shape in enumerate(shapes): 
            shape.available = True
            shape.shapeId = [i]
            shape.c0.shapeIds = []
            shape.c1.shapeIds = []
        for i, shape in enumerate(shapes):  
            shape.c0.shapeIds.append(i)
            shape.c1.shapeIds.append(i)
        for i, shape in enumerate(shapes): 
            shapeIds = shape.c1.shapeIds
            if len(shapeIds) == 2:
                if shapeIds[0] in shape.shapeId:
                    s = shapeIds[1]
                else:
                    s = shapeIds[0]
                if shape != shapes[s]:
                    shape.merge(shapes[s])
                    shape.shapeId += shapes[s].shapeId
                    for j in shape.shapeId:
                        shapes[j] = shape
            shapeIds = shape.c0.shapeIds
            if len(shapeIds) == 2:
                if shapeIds[0] in shape.shapeId:
                    s = shapeIds[1]
                else:
                    s = shapeIds[0]
                if shape != shapes[s]:
                    shape.merge(shapes[s])
                    shape.shapeId += shapes[s].shapeId
                    for j in shape.shapeId:
                        shapes[j] = shape
        for shape in shapes:
            if shape.available:
                shape.consume()
                merged.append(shape)
        print("Ops.merge() :%.2f seconds" % (time.time()-t))
        return merged        
 
class Selectable(object):
    
    """ selectable shapely geoms """
    def __init__(self, geoms, coordsys):
        # selection sets (bitArray)
        self.selections = []
        # selected objects on screen representation
        self.curves = []
        # Rtree to speedup region selections
        self.tree = Qtree(coordsys)
        self.tree.build(geoms)
        # BitArray ids of selected geoms 
        self.ba = BitArray(self.ngeoms)
        # Material to represent selection on screen
        self.mat = self.build_display_mat("Selected", color=bpy.context.user_preferences.themes[0].view_3d.object_selected)
        self.gl = GlDrawOnScreen()
        self.action = None
        self.store_index = 0
    
    @property
    def coordsys(self):
        return self.tree.coordsys
        
    @property
    def geoms(self):
        return self.tree._geoms
       
    @property
    def ngeoms(self):
        return self.tree.ngeoms
        
    @property
    def nsets(self):
        return len(self.selections)
        
    def build_display_mat(self, name, color=(0.2,0.2,0)):
        midx = bpy.data.materials.find(name)    
        if midx < 0:
            mat = bpy.data.materials.new(name)
            mat.use_object_color = True
            mat.emit  = 0.2
            mat.alpha = 0.2
            mat.game_settings.alpha_blend = 'ADD'
            mat.diffuse_color = color
        else:
            mat = bpy.data.materials[midx]
        return mat
    
    def _unselect(self, selection):
        t = time.time()
        for i in selection:
            self.ba.clear(i)
        print("Selectable._unselect() :%.2f seconds" % (time.time()-t))
        
    def _select(self, selection):
        t = time.time()
        for i in selection:
            self.ba.set(i)
        print("Selectable._select() :%.2f seconds" % (time.time()-t))
        
    def _position_3d_from_coord(self, context, coord):
        """return point in local input coordsys
        """
        scene = context.scene
        region = context.region
        rv3d = context.region_data
        view_vector_mouse = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin_mouse = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        loc = intersect_line_plane(ray_origin_mouse, ray_origin_mouse+view_vector_mouse, Vector((0,0,0)), Vector((0,0,1)), False)
        x, y, z = self.coordsys.invert * loc
        return (x, y, z)
    
    def _position_2d_from_coord(self, context, coord):
        """ coord given in local input coordsys
        """
        scene = context.scene
        region = context.region
        rv3d = context.region_data
        loc = view3d_utils.location_3d_to_region_2d(region, rv3d, self.coordsys.world * coord)
        x, y = loc
        return (int(x), int(y))
    
    def _contains(self, context, coord, event):
        t = time.time()
        point = self._position_3d_from_coord(context, coord)
        selection = []
        pt = shapely.geometry.Point(point)
        prepared_pt = shapely.prepared.prep(pt)
        count, gids = self.tree.intersects(pt)
        selection = [i for i in gids if prepared_pt.intersects(self.geoms[i])]
        print("Selectable._contains() :%.2f seconds" % (time.time()-t))
        if event.shift:
            self._unselect(selection)
        else:
            self._select(selection)
        self._draw(context)
    
    def _intersects(self, context, coord, event):
        t = time.time()
        c0 = self._position_3d_from_coord(context, coord)
        c1 = self._position_3d_from_coord(context, (coord[0], event.mouse_region_y))
        c2 = self._position_3d_from_coord(context, (event.mouse_region_x, event.mouse_region_y))
        c3 = self._position_3d_from_coord(context, (event.mouse_region_x, coord[1]))
        poly = shapely.geometry.Polygon([c0,c1,c2,c3])
        prepared_poly = shapely.prepared.prep(poly)
        count, gids = self.tree.intersects(poly)
        if event.alt:
            selection = [i for i in gids if prepared_poly.contains(self.geoms[i])]
        else:
            selection = [i for i in gids if prepared_poly.intersects(self.geoms[i])]
        print("Selectable._intersects() :%.2f seconds" % (time.time()-t))  
        if event.shift:
            self._unselect(selection)
        else:
            self._select(selection)
        self._draw(context)
    
    def _hide(self, context):
        t = time.time()
        if len(self.curves) > 0:
            try:
                for curve in self.curves:
                    data = curve.data
                    context.scene.objects.unlink(curve)
                    bpy.data.objects.remove(curve, do_unlink=True)                
                    if data is None:
                        return
                    name = data.name
                    if bpy.data.curves.find(name) > -1:
                        bpy.data.curves.remove(data, do_unlink=True)
            except:
                pass
            self.curves = [] 
        print("Selectable._hide() :%.2f seconds" % (time.time()-t))      
    
    def _draw(self, context):
        print("Selectable._draw() %s" % (self.coordsys.world))
        t = time.time()
        self._hide(context) 
        selection = [self.geoms[i] for i in self.ba.list]
        if len(selection) > 1000:
            self.curves = [Io.to_curve(context.scene, self.coordsys, selection, 'selection', '3D')]
        else:
            self.curves = Io.to_curves(context.scene, self.coordsys, selection, 'selection', '2D')
        for curve in self.curves:
            curve.color = (1,1,0,1)
            if len(curve.data.materials) < 1:
                curve.data.materials.append(self.mat)
                curve.active_material = self.mat
            curve.select = True
        print("Selectable._draw() :%.2f seconds" % (time.time()-t))
    
    def store(self):
        self.selections.append(self.ba.copy)
        self.store_index = self.nsets
        
    def recall(self):
        if self.nsets > 0:
            if self.store_index < 1:
                self.store_index = self.nsets
            self.store_index -= 1
            self.ba = self.selections[self.store_index].copy

class SelectPoints(Selectable):            
    
    def __init__(self, shapes, coordsys):
        geoms = []
        for shape in shapes:
            if shape.valid:
                for point in shape.points:
                    point.users = 1
        for shape in shapes:
            if shape.valid:
                for point in shape.points:
                    if point.users > 0:
                        point.users = 0
                        geoms.append(point.geom)
        super(SelectPoints, self).__init__(geoms, coordsys)
    
    def _draw(self, context):
        """ override draw method """
        print("SelectPoints._draw()")
        t = time.time()
        self._hide(context) 
        selection = list(self.geoms[i] for i in self.ba.list)
        geom = ShapelyOps.union(selection)
        self.curves = [Io.to_curve(context.scene, self.coordsys, geom.convex_hull, 'selection', '3D')]
        for curve in self.curves:
            curve.color = (1,1,0,1)
            curve.select = True
        print("SelectPoints._draw() :%.2f seconds" % (time.time()-t))
        
    def init(self, pick_tool, context, action):
        # Post selection actions
        self.selectMode = True
        self.object_location = None
        self.startPoint = (0,0)
        self.endPoint = (0,0)
        self.drag = False
        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback, args, 'WINDOW', 'POST_PIXEL')
        self.action = action 
        self._draw(context)
        print("SelectPoints.init()")
    
    def complete(self, context):
        self._hide(context)
        
    def select(self, context, coord, event):
        #print("Selection.select() mouse %s %s" % (abs(event.mouse_region_x - coord[0]), abs(event.mouse_region_y - coord[1])))
        t = time.time()
        if abs(event.mouse_region_x - coord[0]) > 2 and abs(event.mouse_region_y - coord[1]) > 2:
            self._intersects(context, coord, event)
        else:
            self._contains(context, (event.mouse_region_x, event.mouse_region_y), event)
        print("SelectPoints.select() :%.2f seconds" % (time.time()-t))  
    
    def keyboard(self, context, event):
        if event.type in {'A'}:
            if len(self.ba.list) > 0:
                self.ba.none()
            else:    
                self.ba.all()
        elif event.type in {'I'}:
            self.ba.reverse()
        elif event.type in {'S'}:
            self.store()
        elif event.type in {'R'}:
            self.recall()
        elif event.type in {'F'}:
            sel  = [self.geoms[i] for i in self.ba.list]
            if len(sel) > 0:
                scene = context.scene
                geom = ShapelyOps.union(sel)
                if event.alt:
                    tM, w, h = ShapelyOps.min_bounding_rect(geom)
                    x0 = -w/2.0
                    y0 = -h/2.0
                    x1 = w/2.0
                    y1 = h/2.0
                    poly = shapely.geometry.LineString([(x0,y0,0), (x1,y0,0), (x1,y1,0), (x0,y1,0), (x0,y0,0)])
                    result = Io.to_curve(scene, self.coordsys, poly, 'points')
                    result.matrix_world = self.coordsys.world * tM
                    scene.objects.active = result
                else:
                    result = Io.to_curve(scene, self.coordsys, geom.convex_hull, 'points')
                    scene.objects.active = result
            self.ba.none()
            self.complete(context)
        self._draw(context)
        
    def modal(self, context, event):
        if event.type in {'I', 'A', 'S', 'R', 'F'} and event.value == 'PRESS':
            self.keyboard(context, event)
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            self.complete(context)
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            return {'FINISHED'}
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.drag = True
            self.startPoint = (event.mouse_region_x, event.mouse_region_y)
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.drag = False
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
            self.select(context, self.startPoint, event)
        elif event.type == 'MOUSEMOVE':
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
        return {'RUNNING_MODAL'}
    
    def draw_callback(self, _self, context):
        self.gl.String("Select with left mouse, SHIFT to unselect, drag for areas (crossing), ALT contains", 10, 139, 10, self.gl.explanation_colour)
        self.gl.String("ESC or right click when done", 10, 126, 10, self.gl.explanation_colour)
        self.gl.String("A select all / none", 10, 113, 10, self.gl.explanation_colour)
        self.gl.String("I invert selection", 10, 100, 10, self.gl.explanation_colour)
        self.gl.String("S store selection", 10, 87, 10, self.gl.explanation_colour)
        self.gl.String("R retrieve selection", 10, 74, 10, self.gl.explanation_colour)
        self.gl.String("F fill, alt+F for best fitting rectangle", 10, 61, 10, self.gl.explanation_colour)
        if self.drag:
            x0, y0 = self.startPoint
            x1, y1 = self.endPoint
            self.gl.Rectangle(x0, y0, x1, y1, self.gl.line_colour)
       
class SelectLines(Selectable):
    
    def __init__(self, geoms, coordsys):
        super(SelectLines, self).__init__(geoms, coordsys)
    
    def _draw(self, context):
        """ override draw method """
        print("SelectLines._draw()")
        t = time.time()
        self._hide(context) 
        selection = list(self.geoms[i] for i in self.ba.list)
        self.curves = [Io.to_curve(context.scene, self.coordsys, selection, 'selection', '3D')]
        for curve in self.curves:
            curve.color = (1,1,0,1)
            curve.select = True
        print("SelectLines._draw() :%.2f seconds" % (time.time()-t))
        
    def init(self, pick_tool, context, action):
        # Post selection actions
        self.selectMode = True
        self.object_location = None
        self.startPoint = (0,0)
        self.endPoint = (0,0)
        self.drag = False
        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback, args, 'WINDOW', 'POST_PIXEL')
        self.action = action 
        self._draw(context)
        print("SelectLines.init()")
    
    def complete(self, context):
        print("SelectLines.complete()")
        t = time.time()
        self._hide(context)
        scene = context.scene
        selection = list(self.geoms[i] for i in self.ba.list)
        if len(selection) > 0:
            if self.action == 'select':
                result = Io.to_curve(scene, self.coordsys, selection, 'selection')
                scene.objects.active = result
            elif self.action == 'union':
                shapes = Io.geoms_to_shapes(selection)
                merged = ShapeOps.merge(shapes)
                union  = Io.shapes_to_geoms(merged)
                #union  = self.ops.union(selection)
                resopt = ShapelyOps.optimize(union)
                result = Io.to_curve(scene, self.coordsys, resopt, 'union')
                scene.objects.active = result
        print("SelectLines.complete() :%.2f seconds" % (time.time()-t))
    
    def select(self, context, coord, event):
        #print("Selection.select() mouse %s %s" % (abs(event.mouse_region_x - coord[0]), abs(event.mouse_region_y - coord[1])))
        t = time.time()
        if abs(event.mouse_region_x - coord[0]) > 2 and abs(event.mouse_region_y - coord[1]) > 2:
            self._intersects(context, coord, event)
        else:
            self._contains(context, (event.mouse_region_x, event.mouse_region_y), event)
        print("SelectLines.select() :%.2f seconds" % (time.time()-t))  
    
    def keyboard(self, context, event):
        if event.type in {'A'}:
            if len(self.ba.list) > 0:
                self.ba.none()
            else:    
                self.ba.all()
        elif event.type in {'I'}:
            self.ba.reverse()
        elif event.type in {'S'}:
            self.store()
        elif event.type in {'R'}:
            self.recall()
        self._draw(context)
        
    def modal(self, context, event):
        if event.type in {'I', 'A', 'S', 'R'} and event.value == 'PRESS':
            self.keyboard(context, event)
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            self.complete(context)
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            return {'FINISHED'}
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.drag = True
            self.startPoint = (event.mouse_region_x, event.mouse_region_y)
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.drag = False
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
            self.select(context, self.startPoint, event)
        elif event.type == 'MOUSEMOVE':
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
        return {'RUNNING_MODAL'}
    
    def draw_callback(self, _self, context):
        self.gl.String("Select with left mouse, SHIFT to unselect, drag for areas (crossing), ALT contains", 10, 139, 10, self.gl.explanation_colour)
        self.gl.String("ESC or right click when done", 10, 126, 10, self.gl.explanation_colour)
        self.gl.String("A select all / none", 10, 113, 10, self.gl.explanation_colour)
        self.gl.String("I invert selection", 10, 100, 10, self.gl.explanation_colour)
        self.gl.String("S store selection", 10, 87, 10, self.gl.explanation_colour)
        self.gl.String("R retrieve selection", 10, 74, 10, self.gl.explanation_colour)
        if self.drag:
            x0, y0 = self.startPoint
            x1, y1 = self.endPoint
            self.gl.Rectangle(x0, y0, x1, y1, self.gl.line_colour)
         
class SelectPolygons(Selectable):
    
    def __init__(self, geoms, coordsys):
        super(SelectPolygons, self).__init__(geoms, coordsys)
        
    def store_point(self, context, coord, event):
        self.last_point = self._position_3d_from_coord(context, (event.mouse_region_x, event.mouse_region_y))
    """
        pick_tools actions
    """
    def init(self, pick_tool, context, action):
        # Post selection actions
        self.object_location = None
        self.last_point = None
        self.selectMode = True
        self.startPoint = (0,0)
        self.endPoint = (0,0)
        self.drag = False
        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback, args, 'WINDOW', 'POST_PIXEL')
        self.action = action 
        self._draw(context)
        print("SelectPolygons.init()")
    
    def complete(self, context):
        print("SelectPolygons.complete()")
        t = time.time()
        scene = context.scene
        self._hide(context)
        selection = list(self.geoms[i] for i in self.ba.list)
        if len(selection) > 0:            
            if self.action == 'select':
                result = Io.to_curve(scene, self.coordsys, selection, 'selection')
                scene.objects.active = result
            elif self.action == 'union':
                union  = ShapelyOps.union(selection)
                resopt = ShapelyOps.optimize(union)
                result = Io.to_curve(scene, self.coordsys, resopt, 'union')
                scene.objects.active = result
            elif self.action == 'wall':
                union = ShapelyOps.union(selection)
                res = []
                Io.to_wall(scene, self.coordsys, union, context.window_manager.poly_lib.solidify_thickness, 'wall', res)
                if len(res) > 0:
                    scene.objects.active = res[0]
                    bpy.ops.object.join()
            elif self.action == 'object':
                # Ment to create doors and windows
                # currently only output a best fitted rectangle
                # over selection
                tM, w, h  = self.object_location
                #pt = (self.last_point * tM.inverted())
                #if pt.y < 0: #reverse rotation
                #if pt.y > 0: #right
                x0 = -w/2.0
                y0 = -h/2.0
                x1 = w/2.0
                y1 = h/2.0
                poly = shapely.geometry.LineString([(x0,y0,0), (x1,y0,0), (x1,y1,0), (x0,y1,0), (x0,y0,0)])
                result = Io.to_curve(scene, self.coordsys, poly, 'object')
                result.matrix_world = self.coordsys.world * tM
                self.ba.none()
                scene.objects.active = result
        print("SelectPolygons.complete() :%.2f seconds" % (time.time()-t))
    
    def select(self, context, coord, event):
        #print("Selection.select() mouse %s %s" % (abs(event.mouse_region_x - coord[0]), abs(event.mouse_region_y - coord[1])))
        t = time.time()
        if abs(event.mouse_region_x - coord[0]) > 2 and abs(event.mouse_region_y - coord[1]) > 2:
            self._intersects(context, coord, event)
        else:
            self._contains(context, (event.mouse_region_x, event.mouse_region_y), event)
        print("SelectPolygons.select() :%.2f seconds" % (time.time()-t))
        
    def keyboard(self, context, event):
        if event.type in {'A'}:
            if len(self.ba.list) > 0:
                self.ba.none()
            else:    
                self.ba.all()
        elif event.type in {'I'}:
            self.ba.reverse()
        elif event.type in {'S'}:
            self.store()
        elif event.type in {'R'}:
            self.recall()
        elif event.type in {'B'}:
            areas = [self.geoms[i].area for i in self.ba.list]
            area = max(areas)
            self.ba.none()
            for i, geom in enumerate(self.geoms):
                if geom.area > area:
                    self.ba.set(i)
        elif event.type in {'F'}:
            self.selectMode = not self.selectMode
            sel  = [self.geoms[i] for i in self.ba.list]
            if len(sel) > 0:
                geom = ShapelyOps.union(sel)
                tM, w, h = ShapelyOps.min_bounding_rect(geom)
                self.object_location = (tM, w, h)
            self.startPoint = self._position_2d_from_coord(context, tM.translation)
        self._draw(context)
        
    def modal(self, context, event):
        if event.type in {'I', 'A', 'S', 'R', 'F', 'B'} and event.value == 'PRESS':
            self.keyboard(context, event)
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            if self.action == 'object':
                self._hide(context)
            else:
                self.complete(context)
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            return {'FINISHED'}
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.drag = True
            if self.selectMode:
                self.startPoint = (event.mouse_region_x, event.mouse_region_y)
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.drag = False
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
            if self.selectMode:
                self.select(context, self.startPoint, event)
            else:
                self.store_point(context, self.startPoint, event)
                self.complete(context)
            self.selectMode = True
        if event.type == 'MOUSEMOVE':
            self.endPoint = (event.mouse_region_x, event.mouse_region_y)
        return {'RUNNING_MODAL'}
    
    def draw_callback(self, _self, context):
        """
        # draw selection using gl.
        # need cache
        selection = [self.geoms[i] for i in self.ba.list]
        for geom in selection:
            gl =[]
            self.io.geom_to_gl(context, gl, geom)
            for poly in gl:
                self.gl.Polygon(poly, self.selected_colour)
        """
        if self.selectMode:
            self.gl.String("Select with left mouse, SHIFT to unselect, drag for areas (crossing), ALT contains", 10, 139, 10, self.gl.explanation_colour)
            self.gl.String("ESC or right click when done", 10, 126, 10, self.gl.explanation_colour)
            self.gl.String("A select all / none", 10, 113, 10, self.gl.explanation_colour)
            self.gl.String("I invert selection", 10, 100, 10, self.gl.explanation_colour)
            self.gl.String("B select bigger area than current", 10, 87, 10, self.gl.explanation_colour)
            self.gl.String("S store selection", 10, 74, 10, self.gl.explanation_colour)
            self.gl.String("R retrieve selection", 10, 61, 10, self.gl.explanation_colour)
        else:
            self.gl.String("Pick a point on inside", 10, 139, 10, self.gl.explanation_colour)
            self.gl.String("ESC or right click to exit", 10, 126, 10, self.gl.explanation_colour)
        if self.drag:
            x0, y0 = self.startPoint
            x1, y1 = self.endPoint
            if self.selectMode:
                self.gl.Rectangle(x0, y0, x1, y1, self.gl.line_colour)
            else:
                self.gl.Line(x0, y0, x1, y1, self.gl.line_colour)

class TOOLS_OP_PolyLib_Pick2DPoints(Operator):
    bl_idname = "tools.poly_lib_pick_2d_points"
    bl_label = "Pick lines"
    bl_description = "Pick lines"
    bl_options = {'REGISTER', 'UNDO'}
    pass_keys = ['NUMPAD_0', 'NUMPAD_1', 'NUMPAD_3', 'NUMPAD_4',
                 'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8',
                 'NUMPAD_9', 'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE']
    action = StringProperty(name="action", default="select")
    
    @classmethod
    def poll(self, context):
        global vars_dict
        return vars_dict['select_points'] is not None
    
    def modal(self, context, event):
        global vars_dict
        context.area.tag_redraw()
        if event.type in self.pass_keys:
            return {'PASS_THROUGH'}
        return vars_dict['select_points'].modal(context, event)
        
    def invoke(self, context, event):
        global vars_dict
        if vars_dict['select_points'] is None:
            self.report({'WARNING'}, "Use detect before")
            return {'CANCELLED'}
        elif context.space_data.type == 'VIEW_3D':
            vars_dict['select_points'].init(self, context, self.action)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "Active space must be a View3d")
            return {'CANCELLED'}
  
class TOOLS_OP_PolyLib_Pick2DLines(Operator):
    bl_idname = "tools.poly_lib_pick_2d_lines"
    bl_label = "Pick lines"
    bl_description = "Pick lines"
    bl_options = {'REGISTER', 'UNDO'}
    pass_keys = ['NUMPAD_0', 'NUMPAD_1', 'NUMPAD_3', 'NUMPAD_4',
                 'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8',
                 'NUMPAD_9', 'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE']
    action = StringProperty(name="action", default="select")
    
    @classmethod
    def poll(self, context):
        global vars_dict
        return vars_dict['select_lines'] is not None
    
    def modal(self, context, event):
        global vars_dict
        context.area.tag_redraw()
        if event.type in self.pass_keys:
            return {'PASS_THROUGH'}
        return vars_dict['select_lines'].modal(context, event)
        
    def invoke(self, context, event):
        global vars_dict
        if vars_dict['select_lines'] is None:
            self.report({'WARNING'}, "Use detect before")
            return {'CANCELLED'}
        elif context.space_data.type == 'VIEW_3D':
            vars_dict['select_lines'].init(self, context, self.action)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "Active space must be a View3d")
            return {'CANCELLED'}
                 
class TOOLS_OP_PolyLib_Pick2DPolygons(Operator):
    bl_idname = "tools.poly_lib_pick_2d_polygons"
    bl_label = "Pick 2d"
    bl_description = "Pick polygons"
    bl_options = {'REGISTER', 'UNDO'}
    pass_keys = ['NUMPAD_0', 'NUMPAD_1', 'NUMPAD_3', 'NUMPAD_4',
                 'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8',
                 'NUMPAD_9', 'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE']
    action = StringProperty(name="action", default="select")
    
    @classmethod
    def poll(self, context):
        global vars_dict
        return vars_dict['select_polygons'] is not None
        
    def modal(self, context, event):
        global vars_dict
        context.area.tag_redraw()
        if event.type in self.pass_keys:
            return {'PASS_THROUGH'}
        return vars_dict['select_polygons'].modal(context, event)
    
    def invoke(self, context, event):
        global vars_dict
        if vars_dict['select_polygons'] is None:
            self.report({'WARNING'}, "Use detect before")
            return {'CANCELLED'}
        elif context.space_data.type == 'VIEW_3D':
            vars_dict['select_polygons'].init(self, context, self.action)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "Active space must be a View3d")
            return {'CANCELLED'}

class TOOLS_OP_PolyLib_Detect(Operator):
    bl_idname = "tools.poly_lib_detect"
    bl_label = "Detect Polygons"
    bl_description = "Detect polygons from unordered splines"
    bl_options = {'REGISTER', 'UNDO'}
    extend = FloatProperty(name="extend", default=0.01, subtype='DISTANCE', unit='LENGTH', min=0)
    
    @classmethod
    def poll(self, context):
        return len(context.selected_objects) > 0 and context.object is not None and context.object.type == 'CURVE'
    
    def execute(self, context):
        global vars_dict
        print("Detect")
        t = time.time()
        objs = [obj for obj in context.selected_objects if obj.type == 'CURVE']
        
        if len(objs) < 1:
            self.report({'WARNING'}, "Select a curve object before")
            return {'CANCELLED'}
        
        for obj in objs:
            obj.select = False
        
        coordsys = CoordSys(objs)
        
        vars_dict['point_tree'] = Qtree(coordsys, extend=0.5*EPSILON)
        vars_dict['seg_tree']   = Qtree(coordsys, extend=self.extend)
        
        # Shape based union
        shapes = []
        Io.curves_to_shapes(objs, coordsys, context.window_manager.poly_lib.resolution, shapes)
        union = ShapeOps.union(shapes, self.extend)
        
        # output select points
        vars_dict['select_points'] = SelectPoints(shapes, coordsys)
        
        geoms = Io.shapes_to_geoms(union)
        
        # output select_lines
        vars_dict['select_lines'] = SelectLines(geoms, coordsys)
        
        # Shapely based union
        # vars_dict['select_polygons'].io.curves_as_shapely(objs, lines)
        # geoms = vars_dict['select_polygons'].ops.union(lines, self.extend)
        
        result, dangles, cuts, invalids = ShapelyOps.detect_polygons(geoms)
        vars_dict['select_polygons'] = SelectPolygons(result, coordsys)
        
        if len(invalids) > 0:
            errs = Io.to_curve(context.scene, coordsys, invalids, "invalid_polygons")
            err_mat = vars_dict['select_polygons'].build_display_mat("Invalid_polygon", (1,0,0))
            # curve.data.bevel_depth = 0.02
            errs.color = (1,0,0,1)
            if len(errs.data.materials) < 1:
                errs.data.materials.append(err_mat)
                errs.active_material = err_mat
            errs.select = True
            self.report({'WARNING'}, str(len(invalids)) + " invalid polygons detected")
        print("Detect :%.2f seconds polygons:%s invalids:%s" % (time.time()-t, len(result), len(invalids)))
        return {'FINISHED'}
  
class TOOLS_OP_PolyLib_Offset(Operator):
    bl_idname = "tools.poly_lib_offset"
    bl_label = "Offset"
    bl_description = "Offset lines"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(self, context):
        return len(context.selected_objects) > 0 and context.object is not None and context.object.type == 'CURVE'
    
    def execute(self, context):
        wm = context.window_manager.poly_lib
        objs = list(obj for obj in context.selected_objects if obj.type == 'CURVE')
        if len(objs) < 1:
            self.report({'WARNING'}, "Select a curve object before")
            return {'CANCELLED'}
        for obj in objs:
            obj.select = False
        lines = []
        coordsys = Io.curves_to_geoms(objs, wm.resolution, lines)
        offset = []
        for line in lines:
            res = line.parallel_offset(wm.offset_distance, wm.offset_side, resolution=wm.offset_resolution,  join_style=int(wm.offset_join_style), mitre_limit=wm.offset_mitre_limit) 
            offset.append(res)
        result = Io.to_curve(context.scene, coordsys, offset, 'offset')
        return {'FINISHED'}
 
class TOOLS_OP_PolyLib_Simplify(Operator):
    bl_idname = "tools.poly_lib_simplify"
    bl_label = "Simplify"
    bl_description = "Simplify lines"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(self, context):
        return len(context.selected_objects) > 0 and context.object is not None and context.object.type == 'CURVE'
    
    def execute(self, context):
        global vars_dict
        wm = context.window_manager.poly_lib
        objs = [obj for obj in context.selected_objects if obj.type == 'CURVE']
        if len(objs) < 1:
            self.report({'WARNING'}, "Select a curve object before")
            return {'CANCELLED'}
        for obj in objs:
            obj.select = False
        simple = []
        lines = []
        coordsys = Io.curves_to_geoms(objs, wm.resolution, lines)
        for line in lines:
            res = line.simplify(wm.simplify_tolerance, preserve_topology=wm.simplify_preserve_topology) 
            simple.append(res)
        result = Io.to_curve(context.scene, coordsys, simple, 'simplify')
        return {'FINISHED'}    
       
class TOOLS_OP_PolyLib_OutputPolygons(Operator):
    bl_idname = "tools.poly_lib_output_polygons"
    bl_label = "Output Polygons"
    bl_description = "Output all polygons"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(self, context):
        global vars_dict
        return vars_dict['select_polygons'] is not None
        
    def execute(self, context):
        global vars_dict
        result = Io.to_curve(context.scene, vars_dict['select_polygons'].coordsys, vars_dict['select_polygons'].geoms, 'polygons')
        context.scene.objects.active = result
        return {'FINISHED'}
 
class TOOLS_OP_PolyLib_OutputLines(Operator):
    bl_idname = "tools.poly_lib_output_lines"
    bl_label = "Output lines"
    bl_description = "Output all lines"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(self, context):
        global vars_dict
        return vars_dict['select_lines'] is not None
    
    def execute(self, context):
        global vars_dict
        result = Io.to_curve(context.scene, vars_dict['select_lines'].coordsys, vars_dict['select_lines'].geoms, 'lines')
        context.scene.objects.active = result
        return {'FINISHED'}
 
class TOOLS_OP_PolyLib_Solidify(Operator):
    bl_idname = "tools.poly_lib_solidify"
    bl_label = "Extrude"
    bl_description = "Extrude all polygons"
    bl_options = {'REGISTER', 'UNDO'}
    @classmethod
    def poll(self, context):
        return len(context.selected_objects) > 0 and context.object is not None and context.object.type == 'CURVE'
    def execute(self, context):
        wm = context.window_manager.poly_lib
        objs = [obj for obj in context.selected_objects if obj.type == 'CURVE']
        if len(objs) < 1:
            self.report({'WARNING'}, "Select a curve object before")
            return {'CANCELLED'}
        for obj in objs:
            obj.data.dimensions = '2D'
            mod = obj.modifiers.new("Solidify", 'SOLIDIFY')    
            mod.thickness = wm.solidify_thickness
            mod.offset = 1.00
            mod.use_even_offset = True
            mod.use_quality_normals = True
        return {'FINISHED'}
 
class TOOLS_PT_PolyLib(Panel):
    bl_label = "Polygons"
    bl_idname = "TOOLS_PT_PolyLib"
    bl_space_type = "VIEW_3D"
    bl_region_type = "TOOLS"
    bl_category = "Tools"
    
    @classmethod
    def poll(self, context):
        global vars_dict
        return ((vars_dict['select_polygons'] is not None) or
                (context.object is not None and context.object.type == 'CURVE')) 
    
    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        box = row.box()
        row = box.row(align=True)
        row.operator("tools.poly_lib_detect", icon_value=icons_dict["detect"].icon_id, text ='Detect').extend = context.window_manager.poly_lib.extend
        row.prop(context.window_manager.poly_lib, "extend")
        row = box.row(align=True)
        row.prop(context.window_manager.poly_lib, "resolution")
        row = box.row(align=True)
        row.label(text="Polygons")
        row = box.row(align=True)
        row.operator("tools.poly_lib_pick_2d_polygons", icon_value=icons_dict["selection"].icon_id, text ='Select').action = 'select'
        row.operator("tools.poly_lib_pick_2d_polygons", icon_value=icons_dict["union"].icon_id, text ='Union').action = 'union'
        row.operator("tools.poly_lib_output_polygons", icon_value=icons_dict["polygons"].icon_id, text ='All')
        row = box.row(align=True)
        row.operator("tools.poly_lib_pick_2d_polygons", icon_value=icons_dict["union"].icon_id, text ='Wall').action='wall'
        row.prop(context.window_manager.poly_lib, "solidify_thickness")
        row = box.row(align=True)
        #row.operator("tools.poly_lib_pick_2d_polygons", text ='Window').action = 'object'
        #row.operator("tools.poly_lib_pick_2d_polygons", text ='Door').action = 'object'
        row.operator("tools.poly_lib_pick_2d_polygons", text ='Best fit rectangle').action = 'object'
        row = box.row(align=True)
        row.label(text="Lines")
        row = box.row(align=True)
        row.operator("tools.poly_lib_pick_2d_lines", icon_value=icons_dict["selection"].icon_id, text ='Lines').action = 'select'
        row.operator("tools.poly_lib_pick_2d_lines", icon_value=icons_dict["union"].icon_id, text ='Union').action = 'union'
        row.operator("tools.poly_lib_output_lines", icon_value=icons_dict["polygons"].icon_id, text ='All')
        #row = layout.row(align=True)
        #box = row.box()
        #row = box.row(align=True)
        #row.operator("tools.poly_lib_solidify")
        row = box.row(align=True)
        row.label(text="Points")
        row = box.row(align=True)
        row.operator("tools.poly_lib_pick_2d_points", icon_value=icons_dict["selection"].icon_id, text ='Points').action = 'select'
        row = layout.row(align=True)
        box = row.box()
        row = box.row(align=True)
        row.operator("tools.poly_lib_simplify")
        row.prop(context.window_manager.poly_lib, "simplify_tolerance")
        row = box.row(align=True)
        row.prop(context.window_manager.poly_lib, "simplify_preserve_topology")
        row = layout.row(align=True)
        box = row.box()
        row = box.row(align=True)
        row.operator("tools.poly_lib_offset")
        row = box.row(align=True)
        row.prop(context.window_manager.poly_lib, "offset_distance")
        row = box.row(align=True)
        row.prop(context.window_manager.poly_lib, "offset_side")
        row = box.row(align=True)
        row.prop(context.window_manager.poly_lib, "offset_resolution")
        row = box.row(align=True)
        row.prop(context.window_manager.poly_lib, "offset_join_style")
        row = box.row(align=True)
        row.prop(context.window_manager.poly_lib, "offset_mitre_limit")
        
class PolyLibParameters(PropertyGroup):
    bl_idname = 'tools.poly_lib_parameters'
    extend = FloatProperty(name="Extend", description="Extend to closest intersecting segment", default=0.01, subtype='DISTANCE', unit='LENGTH', min=0)
    offset_distance = FloatProperty(name="Distance", default=0.05, subtype='DISTANCE', unit='LENGTH', min=0)
    offset_side = EnumProperty(name="Side", default='left', items = [('left', 'Left', 'Left'),('right', 'Right', 'Right')])
    offset_resolution = IntProperty(name="Resolution", default=16)
    offset_join_style = EnumProperty(name="Style", default='2', items = [('1', 'Round', 'Round'),('2', 'Mitre', 'Mitre'),('3', 'Bevel', 'Bevel')])
    offset_mitre_limit = FloatProperty(name="Mitre limit", default=10.0, subtype='DISTANCE', unit='LENGTH', min=0)
    simplify_tolerance = FloatProperty(name="Tolerance", default=0.01, subtype='DISTANCE', unit='LENGTH', min=0)
    simplify_preserve_topology = BoolProperty(name = "Preserve topology", description="Preserve topology (fast without, but may introduce self crossing)", default = True)
    solidify_thickness = FloatProperty(name="Thickness", default=2.7, subtype='DISTANCE', unit='LENGTH', min=0)
    resolution = IntProperty(name="Bezier resolution", min=0, default=12)
    
@persistent    
def load_handler(dummy):
    global vars_dict
    vars_dict['select_polygons'] = None
    vars_dict['select_lines'] = None
    vars_dict['seg_tree'] = None
    vars_dict['point_tree'] = None
     
def register():
    global icons_dict
    icons_dict = iconsLib.new()
    icons_dir = os.path.join(os.path.dirname(__file__), "icons")
    for icon in os.listdir(icons_dir):
        name, ext = os.path.splitext(icon)
        icons_dict.load(name, os.path.join(icons_dir, icon), 'IMAGE')
    bpy.utils.register_class(TOOLS_OP_PolyLib_Pick2DPolygons)
    bpy.utils.register_class(TOOLS_OP_PolyLib_Pick2DLines)
    bpy.utils.register_class(TOOLS_OP_PolyLib_Pick2DPoints)
    bpy.utils.register_class(TOOLS_OP_PolyLib_OutputPolygons)
    bpy.utils.register_class(TOOLS_OP_PolyLib_OutputLines)
    bpy.utils.register_class(TOOLS_OP_PolyLib_Offset)
    bpy.utils.register_class(TOOLS_OP_PolyLib_Simplify)
    bpy.utils.register_class(TOOLS_OP_PolyLib_Detect)
    bpy.utils.register_class(TOOLS_OP_PolyLib_Solidify)
    bpy.utils.register_class(PolyLibParameters)
    bpy.types.WindowManager.poly_lib = PointerProperty(type = PolyLibParameters)
    bpy.utils.register_class(TOOLS_PT_PolyLib)
    bpy.app.handlers.load_post.append(load_handler)
    
def unregister():
    global icons_dict
    iconsLib.remove(icons_dict)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_Pick2DPolygons)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_Pick2DLines)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_Pick2DPoints)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_Detect)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_OutputPolygons)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_OutputLines)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_Offset)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_Simplify)
    bpy.utils.unregister_class(TOOLS_OP_PolyLib_Solidify)
    bpy.utils.unregister_class(TOOLS_PT_PolyLib)
    bpy.utils.unregister_class(PolyLibParameters)
    bpy.app.handlers.load_post.remove(load_handler)
    del bpy.types.WindowManager.poly_lib
 
if __name__ == "__main__":
    register()
