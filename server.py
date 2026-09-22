from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json, os, sys, tempfile, traceback, gzip, uuid, threading, time
from pathlib import Path

try:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.api
    import ifcopenshell.util.schema
except Exception as e:
    print('BRAK IFCOPENSHELL:', e)
    print('Uruchom instalator START_IFC_VIEWER_4_0.bat')
    raise

ROOT = Path(__file__).resolve().parent
UPLOAD = ROOT / 'uploads'
UPLOAD.mkdir(exist_ok=True)
JOBS = {}
LOCK = threading.Lock()


def safe(v):
    try: return v if v is not None else ''
    except Exception: return ''


def element_info(el):
    def attr(name):
        try: return safe(getattr(el, name, ''))
        except Exception: return ''
    psets = {}
    try:
        for rel in getattr(el, 'IsDefinedBy', []) or []:
            pd = getattr(rel, 'RelatingPropertyDefinition', None)
            if not pd or not pd.is_a('IfcPropertySet'): continue
            vals = {}
            for p in getattr(pd, 'HasProperties', []) or []:
                try:
                    val = p.NominalValue.wrappedValue if p.NominalValue else ''
                except Exception:
                    val = ''
                vals[p.Name] = safe(val)
            psets[pd.Name] = vals
    except Exception:
        pass
    return {
        'id': el.id(), 'guid': attr('GlobalId'), 'type': el.is_a(),
        'name': attr('Name'), 'description': attr('Description'), 'tag': attr('Tag'),
        'psets': psets
    }



def semantic_parent(el):
    """Return the nearest decomposing parent IfcProduct, if present."""
    cur=el
    seen=set()
    while cur is not None:
        try:
            cid=int(cur.id())
            if cid in seen: break
            seen.add(cid)
        except Exception:
            break
        parent=None
        try:
            for rel in getattr(cur,'Decomposes',[]) or []:
                obj=getattr(rel,'RelatingObject',None)
                if obj is not None and obj.is_a('IfcProduct'):
                    parent=obj; break
        except Exception:
            parent=None
        if parent is None: break
        cur=parent
    return cur if cur is not el else None


def load_ifc(path, progress=None):
    model = ifcopenshell.open(str(path))
    if progress:
        progress(phase="opened", processed=0, total=0,
                 message="IFC otwarty. Odczytuję elementy...")

    settings = ifcopenshell.geom.settings()
    try: settings.set(settings.USE_WORLD_COORDS, True)
    except Exception: pass
    try: settings.set(settings.WELD_VERTICES, True)
    except Exception: pass

    verts=[]; faces=[]; objects=[]; offset=0
    products=[]
    try:
        products=model.by_type('IfcProduct')
    except Exception:
        products=[]

    total=len(products)
    if progress:
        progress(phase="geometry", processed=0, total=total,
                 message=f"Przygotowuję geometrię: 0/{total}")

    iterator=ifcopenshell.geom.iterator(
        settings, model, max(1, min(8, os.cpu_count() or 1))
    )

    # initialize() can itself take time for large IFCs, so report this phase.
    if progress:
        progress(phase="geometry_init", processed=0, total=total,
                 message=f"Inicjalizuję silnik geometrii IFC (0/{total})...")

    if iterator.initialize():
        processed=0
        while True:
            shape=iterator.get()
            processed += 1
            try:
                geom=shape.geometry
                vs=list(geom.verts)
                fs=list(geom.faces)
                if vs and fs:
                    verts.extend(vs)
                    faces.extend([x+offset for x in fs])
                    eid=int(shape.id)
                    try: el=model.by_id(eid)
                    except Exception: el=None
                    info=element_info(el) if el else {
                        'id':eid,'guid':'','type':'IfcProduct',
                        'name':'','description':'','tag':'','psets':{}
                    }
                    info['vertexStart']=offset
                    info['vertexCount']=len(vs)//3
                    info['indexStart']=len(faces)-len(fs)
                    info['indexCount']=len(fs)
                    objects.append(info)
                    offset += len(vs)//3
            except Exception as exc:
                # One bad element must not stop the whole model.
                print(f"Geometry warning for iterator item {processed}: {exc}")
            if progress:
                progress(
                    phase="geometry",
                    processed=processed,
                    total=total,
                    message=f"Przetwarzanie geometrii: {processed}/{total}"
                )
            if not iterator.next():
                break

    if progress:
        progress(phase="metrics", processed=total, total=total,
                 message="Obliczam BIM DNA...")

    entity_count=0
    classes={}
    for e in model:
        entity_count += 1
        try:
            t=e.is_a()
            classes[t]=classes.get(t,0)+1
        except Exception:
            pass

    total_products=len(products)
    geom_products=len(objects)
    density=round(100*geom_products/max(1,total_products),1)

    direct_geometry_class_counts={}
    semantic_geometry_class_counts={}
    rolled_part_count=0
    for o in objects:
        try:
            el=model.by_id(int(o.get('id',0)))
        except Exception:
            el=None
        if el is None: continue
        direct=el.is_a()
        direct_geometry_class_counts[direct]=direct_geometry_class_counts.get(direct,0)+1
        semantic=direct
        if direct=='IfcBuildingElementPart':
            parent=semantic_parent(el)
            if parent is not None:
                semantic=parent.is_a()
                rolled_part_count += 1
        semantic_geometry_class_counts[semantic]=semantic_geometry_class_counts.get(semantic,0)+1

    product_class_counts={}
    for p in products:
        if p is not None:
            t=p.is_a(); product_class_counts[t]=product_class_counts.get(t,0)+1
    product_classes=sorted(product_class_counts)

    return {
      'schema':getattr(model,'schema','UNKNOWN'),
      'entityCount':entity_count,
      'productCount':total_products,
      'geometryProductCount':geom_products,
      'triangleCount':len(faces)//3,
      'vertexCount':len(verts)//3,
      'informationDensity':density,
      'classes':classes,
      'productClasses':product_classes,
      'productClassCounts':product_class_counts,
      'directGeometryClassCounts':direct_geometry_class_counts,
      'semanticGeometryClassCounts':semantic_geometry_class_counts,
      'rolledBuildingElementParts':rolled_part_count,
      'objects':objects,
      'positions':verts,
      'indices':faces
    }

def json_bytes(obj):
    # Compact JSON; all model values are plain Python scalars/lists.
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def set_headers(h, code, ctype, length=None, encoding=None, cache=False):
    h.send_response(code)
    h.send_header('Content-Type', ctype)
    if length is not None: h.send_header('Content-Length', str(length))
    if encoding: h.send_header('Content-Encoding', encoding)
    h.send_header('Cache-Control', 'no-store' if not cache else 'public, max-age=0')
    h.send_header('Access-Control-Allow-Origin', '*')
    h.end_headers()


def send_json(h, code, obj, compress=True):
    raw=json_bytes(obj)
    if compress and len(raw)>1024:
        raw=gzip.compress(raw, compresslevel=6)
        set_headers(h,code,'application/json; charset=utf-8',len(raw),'gzip')
    else:
        set_headers(h,code,'application/json; charset=utf-8',len(raw))
    h.wfile.write(raw)



def _owner_history(el):
    try: return getattr(el, 'OwnerHistory', None)
    except Exception: return None


def _ifc_label(model, value):
    # IfcLabel is supported in IFC2X3 and IFC4 and is a safe default for user-entered text.
    return model.create_entity('IfcLabel', str(value))


def _target_elements(model, payload):
    scope=payload.get('scope','selected')
    if scope=='class':
        cls=str(payload.get('ifcClass','')).strip()
        if not cls: raise ValueError('Wybierz klasę IFC.')
        return list(model.by_type(cls))
    eid=int(payload.get('expressId') or 0)
    if not eid: raise ValueError('Najpierw zaznacz element w modelu.')
    el=model.by_id(eid)
    if not el: raise ValueError('Nie znaleziono zaznaczonego elementu.')
    return [el]


def _find_pset(el, pset_name):
    for rel in getattr(el,'IsDefinedBy',[]) or []:
        pd=getattr(rel,'RelatingPropertyDefinition',None)
        if pd and pd.is_a('IfcPropertySet') and str(getattr(pd,'Name',''))==pset_name:
            return rel,pd
    return None,None


def set_property(model, targets, pset_name, prop_name, value):
    changed=0
    for el in targets:
        rel,pset=_find_pset(el,pset_name)
        if pset is None:
            oh=_owner_history(el)
            pset=model.create_entity('IfcPropertySet', GlobalId=ifcopenshell.guid.new(), OwnerHistory=oh,
                                     Name=pset_name, Description=None, HasProperties=[])
            model.create_entity('IfcRelDefinesByProperties', GlobalId=ifcopenshell.guid.new(), OwnerHistory=oh,
                                Name=None, Description=None, RelatedObjects=[el], RelatingPropertyDefinition=pset)
        prop=None
        for p in list(getattr(pset,'HasProperties',[]) or []):
            if str(getattr(p,'Name',''))==prop_name:
                prop=p; break
        if prop and prop.is_a('IfcPropertySingleValue'):
            prop.NominalValue=_ifc_label(model,value)
        else:
            if prop:
                props=[x for x in list(pset.HasProperties or []) if x.id()!=prop.id()]
                pset.HasProperties=props
                try: model.remove(prop)
                except Exception: pass
            newp=model.create_entity('IfcPropertySingleValue', Name=prop_name, Description=None,
                                     NominalValue=_ifc_label(model,value), Unit=None)
            pset.HasProperties=list(pset.HasProperties or [])+[newp]
        changed+=1
    return changed


def delete_property(model, targets, pset_name, prop_name):
    changed=0
    for el in targets:
        rel,pset=_find_pset(el,pset_name)
        if pset is None: continue
        props=list(getattr(pset,'HasProperties',[]) or [])
        hit=[p for p in props if str(getattr(p,'Name',''))==prop_name]
        if not hit: continue
        pset.HasProperties=[p for p in props if p not in hit]
        for p in hit:
            try: model.remove(p)
            except Exception: pass
        # If this was the last property, remove the empty Pset and its relation.
        if not list(getattr(pset,'HasProperties',[]) or []):
            try: model.remove(rel)
            except Exception: pass
            try: model.remove(pset)
            except Exception: pass
        changed+=1
    return changed


def reassign_class(model, targets, ifc_class):
    changed=0
    for el in targets:
        # Official IfcOpenShell API keeps IFC relationships/attributes consistent
        # when changing the entity declaration.
        ifcopenshell.api.run("root.reassign_class", model, product=el, ifc_class=ifc_class)
        changed += 1
    return changed


def reload_job_model(jid):
    with LOCK: job=JOBS.get(jid)
    if not job: raise ValueError('Nie znaleziono aktywnego modelu.')
    path=Path(job['path'])
    result=load_ifc(path)
    with LOCK:
        job=JOBS[jid]
        job['result']=result
        job['result_sent']=False
        job['status']='done'
        job['message']='Model zaktualizowany.'
    return result

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT),**kwargs)

    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path=='/api/health':
            send_json(self,200,{'ok':True,'ifcopenshell':getattr(ifcopenshell,'version','unknown')},compress=False)
            return
        if parsed.path=='/api/download':
            jid=parse_qs(parsed.query).get('id',[''])[0]
            with LOCK: job=JOBS.get(jid)
            if not job or not job.get('path'):
                send_json(self,404,{'error':'Nie znaleziono aktywnego modelu.'},compress=False); return
            path=Path(job['path'])
            if not path.exists():
                send_json(self,404,{'error':'Plik modelu nie istnieje.'},compress=False); return
            data=path.read_bytes()
            filename=job.get('filename','model.ifc')
            stem=Path(filename).stem+'_edited.ifc'
            self.send_response(200)
            self.send_header('Content-Type','application/octet-stream')
            self.send_header('Content-Disposition',f'attachment; filename="{stem}"')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(data); return
        if parsed.path=='/api/job':
            from urllib.parse import parse_qs
            jid=parse_qs(parsed.query).get('id',[''])[0]
            with LOCK: job=JOBS.get(jid)
            if not job:
                send_json(self,404,{'error':'Nie znaleziono zadania.'},compress=False); return
            # Do not send the large result until processing is complete.
            if job['status']=='done':
                result=job.pop('result',None)
                if result is not None:
                    job['result_sent']=True
                    send_json(self,200,{'status':'done','result':result})
                else:
                    send_json(self,200,{'status':'done','message':'Wynik został już odebrany.'},compress=False)
            else:
                send_json(self,200,{k:v for k,v in job.items() if k!='result'},compress=False)
            return
        super().do_GET()

    def do_POST(self):
        if self.path=='/api/class/reassign':
            try:
                n=int(self.headers.get('Content-Length','0'))
                payload=json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
                jid=str(payload.get('job',''))
                with LOCK: job=JOBS.get(jid)
                if not job or not job.get('path'): raise ValueError('Nie znaleziono aktywnego modelu.')
                path=Path(job['path']); model=ifcopenshell.open(str(path))
                ids=[int(x) for x in payload.get('expressIds',[]) if int(x)>0]
                if not ids: raise ValueError('Nie wybrano elementów do zmiany.')
                target_class=str(payload.get('ifcClass','')).strip()
                if not target_class.startswith('Ifc'): raise ValueError('Nieprawidłowa klasa IFC.')
                targets=[model.by_id(x) for x in ids]
                targets=[x for x in targets if x is not None and x.is_a('IfcProduct')]
                count=reassign_class(model,targets,target_class)
                model.write(str(path))
                # Do not rebuild/send the entire mesh inside this POST.
                # Large IFCs can keep the HTTP request open long enough for the browser
                # to report "Failed to fetch". Start a normal background geometry job instead.
                new_jid=uuid.uuid4().hex
                with LOCK:
                    JOBS[new_jid]={'status':'processing','filename':job.get('filename','model.ifc'),'started':time.time(),'phase':'starting','processed':0,'total':0,'message':'Odświeżam model po zmianie klasy...','path':str(path)}
                threading.Thread(target=self.worker,args=(new_jid,path),daemon=True).start()
                send_json(self,202,{'ok':True,'count':count,'job':new_jid,'message':f'Zmieniono klasę IFC dla {count} elementów na {target_class}. Odświeżam widok...'},compress=False)
            except Exception as e:
                traceback.print_exc(); send_json(self,500,{'error':str(e)},compress=False)
            return
        if self.path in ('/api/property/set','/api/property/delete'):
            try:
                n=int(self.headers.get('Content-Length','0'))
                payload=json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
                jid=str(payload.get('job',''))
                with LOCK: job=JOBS.get(jid)
                if not job or not job.get('path'): raise ValueError('Nie znaleziono aktywnego modelu.')
                path=Path(job['path']); model=ifcopenshell.open(str(path))
                targets=_target_elements(model,payload)
                pset=str(payload.get('pset','')).strip(); prop=str(payload.get('property','')).strip()
                if not pset or not prop: raise ValueError('Podaj nazwę Property Set i właściwości.')
                if self.path.endswith('/set'):
                    count=set_property(model,targets,pset,prop,payload.get('value',''))
                    action='Dodano / zmieniono właściwość'
                else:
                    count=delete_property(model,targets,pset,prop)
                    action='Usunięto właściwość'
                model.write(str(path))
                result=reload_job_model(jid)
                send_json(self,200,{'ok':True,'count':count,'message':f'{action} dla {count} elementów.','result':result})
            except Exception as e:
                traceback.print_exc(); send_json(self,500,{'error':str(e)},compress=False)
            return
        if self.path!='/api/load':
            send_json(self,404,{'error':'not found'},compress=False); return
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0: raise ValueError('Pusty plik IFC.')
            data=self.rfile.read(n)
            name=self.headers.get('X-Filename','model.ifc').replace('\\','_').replace('/','_') or 'model.ifc'
            jid=uuid.uuid4().hex
            path=UPLOAD/(jid+'_'+name)
            path.write_bytes(data)
            with LOCK: JOBS[jid]={'status':'processing','filename':name,'started':time.time(),'phase':'starting','processed':0,'total':0,'message':'Uruchamiam IfcOpenShell...','path':str(path)}
            threading.Thread(target=self.worker,args=(jid,path),daemon=True).start()
            send_json(self,202,{'status':'processing','job':jid},compress=False)
        except Exception as e:
            traceback.print_exc()
            send_json(self,500,{'error':str(e)},compress=False)

    def worker(self,jid,path):
        try:
            def report(**kw):
                with LOCK:
                    if jid in JOBS:
                        JOBS[jid].update(kw)
            with LOCK:
                JOBS[jid].update(
                    phase="geometry",
                    processed=0,
                    total=0,
                    message="Otwieram IFC i przygotowuję geometrię..."
                )
            result=load_ifc(path, progress=report)
            with LOCK:
                JOBS[jid]={
                    'status':'done',
                    'filename':path.name.split('_',1)[-1],
                    'result':result,
                    'phase':'done',
                    'processed':result.get('geometryProductCount',0),
                    'total':result.get('productCount',0),
                    'message':'Geometria IFC gotowa.',
                    'path':str(path)
                }
        except Exception as e:
            traceback.print_exc()
            with LOCK:
                JOBS[jid]={
                    'status':'error',
                    'error':str(e),
                    'traceback':traceback.format_exc(),
                    'message':'Błąd podczas przetwarzania IFC.'
                }

    def log_message(self,fmt,*args): print(fmt%args)

if __name__=='__main__':
    port=int(os.environ.get('BIM_DNA_PORT','8765'))
    print(f'BIM DNA IFC Viewer 4.10: http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
