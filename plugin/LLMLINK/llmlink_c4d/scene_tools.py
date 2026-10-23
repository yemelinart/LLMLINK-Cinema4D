"""Cinema 4D operations. All live-document calls run on the main thread."""
import contextlib
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import time
import traceback
import uuid

import c4d
from .execution import BoundedOutput, execute_python
from .code_policy import validate_code


def objects(doc):
    stack = list(reversed(doc.GetObjects()))
    while stack:
        obj = stack.pop()
        yield obj
        stack.extend(reversed(obj.GetChildren()))


def vector(value):
    return [float(value.x), float(value.y), float(value.z)]


def parameter_value(value, depth=0):
    """Hash actual editable values, independent of deferred editor dirty counters."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth > 12:
        return type(value).__name__
    if isinstance(value, c4d.Vector):
        return vector(value)
    if isinstance(value, c4d.Matrix):
        return [vector(value.off), vector(value.v1), vector(value.v2), vector(value.v3)]
    if isinstance(value, c4d.BaseTime):
        return value.Get()
    if isinstance(value, c4d.BaseList2D):
        return {'link': str(hash(value))}
    if isinstance(value, c4d.BaseContainer):
        return sorted((ident, parameter_value(item, depth+1)) for ident, item in value
                      if depth or ident != c4d.ID_ICONCHOOSER_SETTINGS)
    if isinstance(value, (list, tuple)):
        return [parameter_value(item, depth+1) for item in value]
    # Plugin-owned custom datatypes are opaque. Keep a conservative marker;
    # never use repr(), which can contain a new wrapper's address on every read.
    return type(value).__name__


def node_state(node):
    result = [str(hash(node)), node.GetType(), node.GetName(), parameter_value(node.GetDataInstance())]
    if isinstance(node, c4d.BaseObject):
        result += [vector(node.GetAbsPos()), vector(node.GetAbsRot()), vector(node.GetAbsScale())]
    if isinstance(node, c4d.PointObject):
        geometry = hashlib.sha256()
        for point in node.GetAllPoints():
            geometry.update(struct.pack('!ddd', point.x, point.y, point.z))
        if isinstance(node, c4d.PolygonObject):
            for polygon in node.GetAllPolygons():
                geometry.update(struct.pack('!iiii', polygon.a, polygon.b, polygon.c, polygon.d))
        result.append(geometry.hexdigest())
    tracks = []
    for track in node.GetCTracks():
        curve = track.GetCurve()
        keys = []
        for index in range(curve.GetKeyCount()):
            key = curve.GetKey(index)
            keys.append((key.GetTime().Get(), key.GetValue(), key.GetInterpolation()))
        tracks.append([parameter_value(track.GetDataInstance()), keys])
    result.append(tracks)
    return result


def snapshot(doc):
    if doc is None:
        raise RuntimeError('Open a Cinema 4D document first')
    rows = []
    fingerprint = []
    count = 0
    for obj in objects(doc):
        count += 1
        fingerprint.append([node_state(obj), str(hash(obj.GetUp())) if obj.GetUp() else '',
                            [node_state(tag) for tag in obj.GetTags()]])
        if len(rows) >= 300:
            continue
        item = {'id': str(hash(obj)), 'name': obj.GetName(), 'type': obj.GetType(),
                'type_name': obj.GetTypeName(), 'parent': obj.GetUp().GetName() if obj.GetUp() else '',
                'location': vector(obj.GetAbsPos()), 'rotation': vector(obj.GetAbsRot()),
                'scale': vector(obj.GetAbsScale()), 'dimensions': vector(obj.GetRad() * 2),
                'tags': [{'type': tag.GetType(), 'name': tag.GetName()} for tag in obj.GetTags()],
                'animation': [{'name': track.GetName(), 'keys': track.GetCurve().GetKeyCount()}
                              for track in obj.GetCTracks()]}
        if isinstance(obj, c4d.PolygonObject):
            item.update(points=obj.GetPointCount(), polygons=obj.GetPolygonCount())
        rows.append(item)
    materials = [{'name': mat.GetName(), 'type': mat.GetType()} for mat in doc.GetMaterials()]
    rd = doc.GetActiveRenderData()
    material_states = []
    for material in doc.GetMaterials():
        shaders = []
        shader = material.GetFirstShader()
        stack = [shader] if shader else []
        while stack:
            shader = stack.pop()
            shaders.append(node_state(shader))
            if shader.GetNext():stack.append(shader.GetNext())
            if shader.GetDown():stack.append(shader.GetDown())
        material_states.append([node_state(material), shaders])
    draw = doc.GetActiveBaseDraw()
    camera = draw.GetSceneCamera(doc) if draw else None
    fingerprint.extend([doc.GetTime().Get(), doc.GetFps(), doc.GetMinTime().Get(), doc.GetMaxTime().Get(),
                        material_states, parameter_value(rd.GetDataInstance()),
                        str(hash(camera)) if camera else '', [str(hash(obj)) for obj in doc.GetActiveObjects(0)]])
    revision = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    return {'application': 'Cinema 4D', 'version': c4d.GetC4DVersion(),
            'document': str(hash(doc)), 'name': doc.GetDocumentName(),
            'path': doc.GetDocumentPath(), 'revision': revision, 'object_count': count,
            'objects': rows, 'truncated': count > len(rows), 'materials': materials[:100],
            'selection': [obj.GetName() for obj in doc.GetActiveObjects(0)],
            'frame': doc.GetTime().GetFrame(doc.GetFps()), 'fps': doc.GetFps(),
            'frame_start': doc.GetMinTime().GetFrame(doc.GetFps()),
            'frame_end': doc.GetMaxTime().GetFrame(doc.GetFps()),
            'render': {'width': int(rd[c4d.RDATA_XRES]), 'height': int(rd[c4d.RDATA_YRES]),
                       'engine': int(rd[c4d.RDATA_RENDERENGINE])}}


def save_copy(doc, path):
    clone = doc.GetClone(c4d.COPYFLAGS_0)
    if clone is None:
        raise RuntimeError('Could not clone the scene; no change was applied')
    flags = c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST
    if not c4d.documents.SaveDocument(clone, str(path), flags, c4d.FORMAT_C4DEXPORT):
        raise RuntimeError('Could not save the scene copy; no change was applied')
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError('Scene copy was not written')
    return str(path)


class Helpers:
    def __init__(self, doc, output, allowed_assets=()):
        self.doc, self.output = doc, Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.render_request = None
        self.files = []
        self._allowed_assets = {str(Path(path).expanduser().resolve()) for path in allowed_assets if isinstance(path,str)}

    def import_asset(self, path):
        source = Path(path).expanduser().resolve(strict=True)
        if str(source) not in self._allowed_assets:
            raise ValueError('Attach this asset to the current message before importing it')
        if not source.is_file() or source.suffix.lower() not in {'.c4d','.obj','.fbx','.abc','.3ds','.dae','.stl','.ply'}:
            raise ValueError('Unsupported scene asset; attach a supported 3D file')
        if not c4d.documents.MergeDocument(self.doc,str(source),c4d.SCENEFILTER_OBJECTS|c4d.SCENEFILTER_MATERIALS,None):
            raise RuntimeError('Cinema 4D could not import the attached asset')
        print('Imported attached asset:',source.name)
        return True

    def objects(self):
        return list(objects(self.doc))

    def find(self, name):
        found = [obj for obj in objects(self.doc) if obj.GetName() == name]
        if len(found) != 1:
            raise ValueError('Expected one object named %r; found %d' % (name, len(found)))
        return found[0]

    def set_key(self, obj, parameter_id, frame, value):
        desc = parameter_id if isinstance(parameter_id, c4d.DescID) else c4d.DescID(c4d.DescLevel(parameter_id, c4d.DTYPE_REAL, 0))
        self.doc.AddUndo(c4d.UNDOTYPE_CHANGE, obj)
        track = obj.FindCTrack(desc)
        if track is None:
            track = c4d.CTrack(obj, desc)
            obj.InsertTrackSorted(track)
        curve = track.GetCurve()
        added = curve.AddKey(c4d.BaseTime(frame, self.doc.GetFps()))
        if not added:
            raise RuntimeError('Cannot add animation key')
        added['key'].SetValue(curve, float(value))
        return added['nidx']

    def output_path(self, name, suffix):
        label = re.sub(r'[^\w.-]+', '-', str(name), flags=re.UNICODE).strip('.-')[:64] or 'result'
        return self.output / ('%s-%s-%s%s' % (label, time.strftime('%Y%m%d-%H%M%S'), uuid.uuid4().hex[:8], suffix))

    def save_scene(self, name='scene'):
        if self.render_request:
            raise ValueError('Save the scene in a separate step from rendering')
        path = save_copy(self.doc, self.output_path(name, '.c4d'))
        self.files.append(path)
        print('Saved scene copy:', path)
        return path

    def render(self, width=640, height=480, engine='standard', name='render'):
        if self.render_request or self.files:
            raise ValueError('Use one render per step, separately from saving')
        if type(width) is not int or type(height) is not int or not (64 <= width <= 4096 and 64 <= height <= 4096):
            raise ValueError('Render dimensions must be integers from 64 to 4096')
        if engine not in ('standard', 'current'):
            raise ValueError('Renderer must be standard or current')
        self.render_request = {'width': width, 'height': height, 'engine': engine,
                               'path': str(self.output_path(name, '.png'))}
        print('Render requested; completion will be reported after the PNG is saved.')
        return dict(self.render_request)


class RenderWorker(c4d.threading.C4DThread):
    """Only touches its detached document clone. Never touches editor UI."""
    def __init__(self, clone, request):
        super().__init__()
        self.clone, self.request = clone, request
        self.result = None
        self.progress = 0.0
        self.deadline = time.monotonic() + 1800

    def TestDBreak(self):
        return time.monotonic() > self.deadline

    def Main(self):
        try:
            rd = self.clone.GetActiveRenderData().GetDataInstance().GetClone(c4d.COPYFLAGS_0)
            rd[c4d.RDATA_XRES], rd[c4d.RDATA_YRES] = self.request['width'], self.request['height']
            rd[c4d.RDATA_SAVEIMAGE] = False
            rd[c4d.RDATA_MULTIPASS_SAVEIMAGE] = False
            rd[c4d.RDATA_FRAMESEQUENCE] = c4d.RDATA_FRAMESEQUENCE_CURRENTFRAME
            if self.request['engine'] == 'standard':
                rd[c4d.RDATA_RENDERENGINE] = c4d.RDATA_RENDERENGINE_STANDARD
            bitmap = c4d.bitmaps.BaseBitmap()
            if bitmap.Init(self.request['width'], self.request['height'], 24) != c4d.IMAGERESULT_OK:
                raise RuntimeError('Could not allocate the render bitmap')
            def progress(value, kind):
                self.progress = float(value)
            result = c4d.documents.RenderDocument(self.clone, rd, bitmap,
                                                 c4d.RENDERFLAGS_EXTERNAL, self.Get(), prog=progress)
            if result != c4d.RENDERRESULT_OK:
                raise RuntimeError('Cinema 4D render failed or was cancelled (code %s)' % result)
            if self.TestBreak():
                raise RuntimeError('Render cancelled before saving')
            path = Path(self.request['path'])
            result = bitmap.Save(str(path), c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_0)
            if result != c4d.IMAGERESULT_OK or not path.is_file() or not path.stat().st_size:
                raise RuntimeError('Rendered PNG could not be saved')
            self.result = {'ok': True, 'files': [str(path)], 'render': dict(self.request),
                           'output': 'Render completed; PNG saved: ' + str(path)}
        except Exception as exc:
            self.result = {'ok': False, 'error': str(exc), 'failure_kind': 'execution'}


class SceneExecutor:
    def __init__(self, data):
        self.data = Path(data)
        self.render = None
        self.deferred = None

    def execute(self, command):
        result = {'type': 'result', 'id': command['id'], 'operation': command.get('operation'), 'ok': False}
        try:
            if time.time() > command.get('expires_at', 0):
                raise ValueError('Operation expired before Cinema 4D received it')
            doc = c4d.documents.GetActiveDocument()
            scene = snapshot(doc)
            result['scene'] = scene
            if command['operation'] == 'inspect':
                result['ok'] = True
                return result
            if command['operation'] != 'python' or self.render:
                raise ValueError('Unsupported operation or a render is already running')
            expected = command.get('expected') or {}
            if any(expected.get(key) != scene[key] for key in ('document', 'revision')):
                raise ValueError('Scene changed after planning or approval. Send the task again to replan.')
            code = command.get('code', '')
            if not isinstance(code, str) or not code.strip() or len(code) > 50000:
                raise ValueError('Invalid Python step')
            compiled = validate_code(code)
            c4d.StopAllThreads()
            folder = self.data / 'checkpoints'
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:10] + '.c4d')
            result['checkpoint'] = save_copy(doc, path)
        except Exception as exc:
            result.update(error=str(exc), failure_kind='precondition', executed=False)
            return result
        output = BoundedOutput()
        helper = Helpers(doc, self.data / 'output', command.get('assets',[]))
        try:
            doc.StartUndo()
            try:
                namespace = {'__name__': '__llmlink__', 'c4d': c4d, 'doc': doc, 'math': math, 'llmlink': helper}
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    execute_python(compiled, namespace, seconds=8)
            finally:
                doc.EndUndo()
                c4d.EventAdd()
            result.update(ok=True, executed=True, output=output.getvalue(), files=helper.files, scene=snapshot(doc))
            if helper.render_request:
                clone = doc.GetClone(c4d.COPYFLAGS_0)
                if clone is None:
                    raise RuntimeError('Cannot clone the document for rendering')
                self.render = RenderWorker(clone, helper.render_request)
                self.deferred = result
                if not self.render.Start():
                    self.render = None
                    self.deferred = None
                    raise RuntimeError('Cannot start render worker')
                return None
        except BaseException as exc:
            frames = traceback.extract_tb(exc.__traceback__)
            frame = next((frame for frame in reversed(frames) if frame.filename == '<LLMLINK Cinema4D>'), None)
            result.update(ok=False, executed=True, error='%s: %s' % (type(exc).__name__, exc),
                          failure_kind='execution', output=output.getvalue(), files=helper.files,
                          error_line=frame.lineno if frame else None,
                          code_context=code.splitlines()[frame.lineno-1][:1500] if frame else '',
                          api_hint='Inspect the measured partial scene. Repair only what failed; do not recreate successful objects.')
            try:
                result['scene'] = snapshot(doc)
            except Exception as measurement_error:
                result.pop('scene', None)
                result['measurement_error'] = str(measurement_error)
        return result

    def poll_render(self):
        if not self.render or self.render.IsRunning():
            return None
        self.render.Wait(False)
        result = self.deferred
        render_result = self.render.result or {'ok': False, 'error': 'Render ended without a result'}
        result.update(render_result)
        # The editor may have changed while its detached clone was rendering.
        self.render = self.deferred = None
        try:
            result['scene'] = snapshot(c4d.documents.GetActiveDocument())
        except Exception as exc:
            result.pop('scene', None)
            result['measurement_error'] = str(exc)
        return result

    def cancel(self):
        if self.render:
            self.render.End(False)

    def shutdown(self):
        if self.render:
            self.render.End(True)
            self.render = self.deferred = None
