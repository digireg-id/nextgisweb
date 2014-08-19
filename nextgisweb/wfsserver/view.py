# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from pyramid.response import Response

from ..resource import Widget, resource_factory
from .model import Service

from .third_party.FeatureServer.Server import Server, FeatureServerException

from nextgis_to_fs import NextgiswebDatasource


NS_XLINK = 'http://www.w3.org/1999/xlink'


class ServiceWidget(Widget):
    resource = Service
    operation = ('create', 'update')
    amdmod = 'ngw-wfsserver/ServiceWidget'


def handler(obj, request):
    if request.params.get('SERVICE') != 'WFS':
        return

    req = request.params.get('REQUEST')

    params = {
        'service': request.params.get('SERVICE'),
        'request': req,
        'typename': request.params.get('TYPENAME'),
        'srsname': request.params.get('SRSNAME'),
        'version': request.params.get('VERSION'),
        'maxfeatures': request.params.get('MAXFEATURES'),
        'startfeature': request.params.get('STARTFEATURE'),
        'filter': request.params.get('FILTER'),
    }
    # None values can cause parsing errors in featureserver. So delete 'Nones':
    params = {key:params[key] for key in params if params[key] is not None}


    datasources = {l.keyname: NextgiswebDatasource(l.keyname,
        layer=l.resource,
        title=l.display_name) for l in obj.layers
    }
    sourcenames = '/'.join([sourcename for sourcename in datasources])

    server = Server(datasources)
    base_path = request.path_url

    try:
        result = server.dispatchRequest(base_path=base_path,
                                    path_info='/'+sourcenames, params=params)
    except FeatureServerException as e:
        data = e.data
        content_type = e.mime
        return Response(data, content_type=content_type)

    if req.lower() in ['getcapabilities', 'describefeaturetype']:
        content_type, resxml = result
        resp = Response(resxml, content_type=content_type)
        return resp
    elif req.lower() == 'getfeature':
        data = result.getData()
        return Response(data, content_type=result.content_type)
    else:
        print "UNKNOWN request!!!!!"

def setup_pyramid(comp, config):
    config.add_route(
        'wfsclient.wfs', '/resource/{id:\d+}/wfs',
        factory=resource_factory, client=('id',)
    ).add_view(handler, context=Service)