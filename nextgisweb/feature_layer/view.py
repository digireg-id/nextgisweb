# -*- coding: utf-8 -*-
import json
from types import MethodType
from collections import OrderedDict

import geojson
from pyramid.response import Response
from pyramid.renderers import render_to_response

from ..resource import (
    Resource,
    ResourceScope,
    DataStructureScope,
    DataScope,
    resource_factory,
    Widget)
from ..geometry import geom_from_wkt
from ..object_widget import ObjectWidget, CompositeWidget
from ..pyramidcomp import viewargs
from .. import dynmenu as dm

from .interface import IFeatureLayer
from .extension import FeatureExtension


class ComplexEncoder(geojson.GeoJSONEncoder):
    def default(self, obj):
        try:
            return geojson.GeoJSONEncoder.default(self, obj)
        except TypeError:
            return str(obj)


class FeatureLayerFieldsWidget(Widget):
    interface = IFeatureLayer
    operation = ('update', )
    amdmod = 'ngw-feature-layer/FieldsWidget'


PD_READ = DataScope.read
PD_WRITE = DataScope.write

PDS_R = DataStructureScope.read
PDS_W = DataStructureScope.write

PR_R = ResourceScope.read


@viewargs(renderer='nextgisweb:feature_layer/template/feature_browse.mako')
def feature_browse(request):
    request.resource_permission(PD_READ)
    request.resource_permission(PDS_R)
    return dict(obj=request.context, subtitle=u"Таблица объектов",
                maxwidth=True, maxheight=True)


@viewargs(renderer='nextgisweb:feature_layer/template/show.mako')
def feature_show(request):
    request.resource_permission(PD_READ)

    feature_id = int(request.matchdict['feature_id'])

    ext_mid = OrderedDict()
    for k, ecls in FeatureExtension.registry._dict.iteritems():
        if hasattr(ecls, 'display_widget'):
            ext_mid[k] = ecls.display_widget

    return dict(
        obj=request.context,
        subtitle=u"Объект #%d" % feature_id,
        feature_id=feature_id,
        ext_mid=ext_mid)


@viewargs(renderer='nextgisweb:feature_layer/template/widget.mako')
def feature_update(request):
    request.resource_permission(PD_WRITE)

    feature_id = int(request.matchdict['feature_id'])

    ext_mid = OrderedDict()
    for k, ecls in FeatureExtension.registry._dict.iteritems():
        if hasattr(ecls, 'editor_widget'):
            ext_mid[k] = ecls.editor_widget

    fields = []
    for f in request.context.fields:
        fields.append(OrderedDict((
            ('keyname', f.keyname),
            ('datatype', f.datatype),
        )))

    return dict(
        obj=request.context,
        feature_id=feature_id,
        ext_mid=ext_mid, fields=fields,
        subtitle=u"Объект #%d" % feature_id,
        maxheight=True)


@viewargs(context=IFeatureLayer)
def feature_geojson(request):
    request.resource_permission(PD_READ)

    # Класс обертка добавляющая информацию о системе координат в геоинтерфейс
    # результата запроса векторного слоя

    class CRSProxy(object):
        def __init__(self, query):
            self.query = query

        @property
        def __geo_interface__(self):
            result = self.query.__geo_interface__

            # TODO: Нужен корректный способ генерации имени СК, пока по ID

            result['crs'] = dict(type='name', properties=dict(
                name='EPSG:%d' % request.context.srs_id))
            return result

    query = request.context.feature_query()
    query.geom()

    content_disposition = ('attachment; filename=%d.geojson'
                           % request.context.id)

    result = CRSProxy(query())

    return Response(
        geojson.dumps(result, ensure_ascii=False, cls=ComplexEncoder),
        content_type=b'application/json',
        content_disposition=content_disposition)


def field_collection(request):
    request.resource_permission(PDS_R)
    return [f.to_dict() for f in request.context.fields]


def store_collection(layer, request):
    request.resource_permission(PD_READ)

    query = layer.feature_query()

    http_range = request.headers.get('range', None)
    if http_range and http_range.startswith('items='):
        first, last = map(int, http_range[len('items='):].split('-', 1))
        query.limit(last - first + 1, first)

    field_prefix = json.loads(request.headers.get('x-field-prefix', '""'))
    pref = lambda (f): field_prefix + f

    field_list = json.loads(request.headers.get('x-field-list', "[]"))
    if len(field_list) > 0:
        query.fields(*field_list)

    box = request.headers.get('x-feature-box', None)
    if box:
        query.box()

    like = request.params.get('like', '')
    if like != '':
        query.like(like)

    features = query()

    result = []
    for fobj in features:
        fdata = dict(
            [(pref(k), v) for k, v in fobj.fields.iteritems()],
            id=fobj.id, label=fobj.label)
        if box:
            fdata['box'] = fobj.box.bounds

        result.append(fdata)

    headers = dict()
    headers["Content-Type"] = 'application/json'

    if http_range:
        total = features.total_count
        last = min(total - 1, last)
        headers['Content-Range'] = 'items %d-%s/%d' % (first, last, total)

    return Response(json.dumps(result, cls=ComplexEncoder), headers=headers)


def store_item(layer, request):
    request.resource_permission(PD_READ)

    box = request.headers.get('x-feature-box', None)
    ext = request.headers.get('x-feature-ext', None)

    query = layer.feature_query()
    query.filter_by(id=request.matchdict['feature_id'])

    if box:
        query.box()

    feature = list(query())[0]

    result = dict(
        feature.fields,
        id=feature.id, layerId=layer.id,
        fields=feature.fields
    )

    if box:
        result['box'] = feature.box.bounds

    if ext:
        result['ext'] = dict()
        for extcls in FeatureExtension.registry:
            extension = extcls(layer=layer)
            result['ext'][extcls.identity] = extension.feature_data(feature)

    return Response(
        json.dumps(result, cls=ComplexEncoder),
        content_type='application/json')


def setup_pyramid(comp, config):
    DBSession = comp.env.core.DBSession

    class LayerFieldsWidget(ObjectWidget):

        def is_applicable(self):
            return self.operation == 'edit'

        def populate_obj(self):
            obj = self.obj
            data = self.data

            if 'feature_label_field_id' in data:
                obj.feature_label_field_id = data['feature_label_field_id']

            fields = dict(map(lambda fd: (fd['id'], fd), data['fields']))
            for f in obj.fields:
                if f.id in fields:

                    if 'display_name' in fields[f.id]:
                        f.display_name = fields[f.id]['display_name']

                    if 'grid_visibility' in fields[f.id]:
                        f.grid_visibility = fields[f.id]['grid_visibility']

        def widget_module(self):
            return 'feature_layer/LayerFieldsWidget'

        def widget_params(self):
            result = super(LayerFieldsWidget, self).widget_params()

            if self.obj:
                result['value'] = dict(
                    fields=map(lambda f: f.to_dict(), self.obj.fields),
                    feature_label_field_id=self.obj.feature_label_field_id,
                )

            return result

    comp.LayerFieldsWidget = LayerFieldsWidget

    def identify(request):
        """ Сервис идентификации объектов на слоях, поддерживающих интерфейс
        IFeatureLayer """

        srs = int(request.json_body['srs'])
        geom = geom_from_wkt(request.json_body['geom'], srid=srs)
        layers = map(int, request.json_body['layers'])

        layer_list = DBSession.query(Resource).filter(Resource.id.in_(layers))

        result = dict()

        # Количество объектов для всех слоев
        feature_count = 0

        for layer in layer_list:
            if not layer.has_permission(DataScope.read, request.user):
                result[layer.id] = dict(error="Forbidden")

            elif not IFeatureLayer.providedBy(layer):
                result[layer.id] = dict(error="Not implemented")

            else:
                query = layer.feature_query()
                query.intersects(geom)

                # Ограничиваем кол-во идентифицируемых объектов по 10 на слой,
                # иначе ответ может оказаться очень большим.
                query.limit(10)

                features = [
                    dict(id=f.id, layerId=layer.id,
                         label=f.label, fields=f.fields)
                    for f in query()
                ]

                # Добавляем в результаты идентификации название
                # родительского ресурса (можно использовать в случае,
                # если на клиенте нет возможности извлечь имя слоя по
                # идентификатору)
                if layer.parent.has_permission(PR_R, request.user):
                    for feature in features:
                        feature['parent'] = layer.parent.display_name

                result[layer.id] = dict(
                    features=features,
                    featureCount=len(features)
                )

                feature_count += len(features)

        result["featureCount"] = feature_count

        return Response(
            json.dumps(result, cls=ComplexEncoder),
            content_type='application/json')

    config.add_route(
        'feature_layer.identify', '/feature_layer/identify',
        client=(),
    ).add_view(identify)

    config.add_route(
        'feature_layer.feature.browse',
        '/resource/{id:\d+}/feature/',
        factory=resource_factory,
        client=('id', )
    ).add_view(feature_browse, context=IFeatureLayer)

    config.add_route(
        'feature_layer.feature.show',
        '/resource/{id:\d+}/feature/{feature_id:\d+}',
        factory=resource_factory,
        client=('id', 'feature_id')
    ).add_view(feature_show, context=IFeatureLayer)

    config.add_route(
        'feature_layer.feature.update',
        '/resource/{id:\d+}/feature/{feature_id}/update',
        factory=resource_factory,
        client=('id', 'feature_id')
    ).add_view(feature_update, context=IFeatureLayer)

    config.add_route(
        'feature_layer.field', '/resource/{id:\d+}/field/',
        factory=resource_factory,
        client=('id', )
    ).add_view(field_collection, context=IFeatureLayer, renderer='json')

    config.add_route(
        'feature_layer.store',
        '/resource/{id:\d+}/store/',
        factory=resource_factory, client=('id', )
    ).add_view(store_collection, context=IFeatureLayer)

    config.add_route(
        'feature_layer.store.item',
        '/resource/{id:\d+}/store/{feature_id:\d+}',
        factory=resource_factory,
        client=('id', 'feature_id')
    ).add_view(store_item, context=IFeatureLayer)

    config.add_route(
        'feature_layer.geojson',
        '/resource/{id:\d+}/geojson/',
        factory=resource_factory
    ).add_view(feature_geojson)

    def client_settings(self, request):
        return dict(
            extensions=dict(
                map(
                    lambda ext: (ext.identity, dict(
                        displayWidget=ext.display_widget
                    )),
                    FeatureExtension.registry
                )
            ),
            identify=dict(
                attributes=self.settings['identify.attributes']
            ),
        )

    comp.client_settings = MethodType(client_settings, comp, comp.__class__)

    # Расширения меню слоя
    class LayerMenuExt(dm.DynItem):

        def build(self, args):
            if IFeatureLayer.providedBy(args.obj):
                yield dm.Label('feature_layer', u"Векторный слой")

                yield dm.Link(
                    'feature_layer/feature-browse', u"Таблица объектов",
                    lambda args: args.request.route_url(
                        "feature_layer.feature.browse",
                        id=args.obj.id))

                yield dm.Link(
                    'feature_layer/geojson', u"Данные GeoJSON",
                    lambda args: args.request.route_url(
                        "feature_layer.geojson",
                        id=args.obj.id))

    Resource.__dynmenu__.add(LayerMenuExt())

    Resource.__psection__.register(
        key='fields', title=u"Атрибуты",
        template="nextgisweb:feature_layer/template/section_fields.mako",
        is_applicable=lambda (obj): IFeatureLayer.providedBy(obj))
