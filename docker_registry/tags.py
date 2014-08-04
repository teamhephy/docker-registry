# -*- coding: utf-8 -*-

import datetime
import logging
import re
import time

import flask
import gevent
import requests

from docker_registry.core import compat
from docker_registry.core import exceptions
json = compat.json

from . import storage
from . import toolkit
from .app import app
from .lib import mirroring
from .lib import signals


store = storage.load()
logger = logging.getLogger(__name__)
RE_USER_AGENT = re.compile('([^\s/]+)/([^\s/]+)')


@app.route('/v1/repositories/<path:repository>/properties', methods=['PUT'])
@toolkit.parse_repository_name
@toolkit.requires_auth
def set_properties(namespace, repository):
    logger.debug("[set_access] namespace={0}; repository={1}".format(namespace,
                 repository))
    data = None
    try:
        # Note(dmp): unicode patch
        data = json.loads(flask.request.data.decode('utf8'))
    except ValueError:
        pass
    if not data or not isinstance(data, dict):
        return toolkit.api_error('Invalid data')
    private_flag_path = store.private_flag_path(namespace, repository)
    if (data['access'] == 'private'
       and not store.is_private(namespace, repository)):
        store.put_content(private_flag_path, '')
    elif (data['access'] == 'public'
          and store.is_private(namespace, repository)):
        # XXX is this necessary? Or do we know for sure the file exists?
        try:
            store.remove(private_flag_path)
        except Exception:
            pass
    return toolkit.response()


@app.route('/v1/repositories/<path:repository>/properties', methods=['GET'])
@toolkit.parse_repository_name
@toolkit.requires_auth
def get_properties(namespace, repository):
    logger.debug("[get_access] namespace={0}; repository={1}".format(namespace,
                 repository))
    is_private = store.is_private(namespace, repository)
    return toolkit.response({
        'access': 'private' if is_private else 'public'
    })


def get_tags(namespace, repository):
    tag_path = store.tag_path(namespace, repository)
    greenlets = {}
    for fname in store.list_directory(tag_path):
        full_tag_name = fname.split('/').pop()
        if not full_tag_name.startswith('tag_'):
            continue
        tag_name = full_tag_name[4:]
        greenlets[tag_name] = gevent.spawn(
            store.get_content,
            store.tag_path(namespace, repository, tag_name),
        )
    gevent.joinall(greenlets.values())
    return dict((k, g.value) for (k, g) in greenlets.items())


@app.route('/v1/repositories/<path:repository>/tags', methods=['GET'])
@toolkit.parse_repository_name
@toolkit.requires_auth
@mirroring.source_lookup_tag
def _get_tags(namespace, repository):
    logger.debug("[get_tags] namespace={0}; repository={1}".format(namespace,
                 repository))
    try:
        data = get_tags(namespace=namespace, repository=repository)
    except exceptions.FileNotFoundError:
        return toolkit.api_error('Repository not found', 404)
    return toolkit.response(data)


@app.route('/v1/repositories/<path:repository>/tags/<tag>', methods=['GET'])
@toolkit.parse_repository_name
@toolkit.requires_auth
@mirroring.source_lookup_tag
def get_tag(namespace, repository, tag):
    logger.debug("[get_tag] namespace={0}; repository={1}; tag={2}".format(
                 namespace, repository, tag))
    data = None
    tag_path = store.tag_path(namespace, repository, tag)
    try:
        data = store.get_content(tag_path)
    except exceptions.FileNotFoundError:
        return toolkit.api_error('Tag not found', 404)
    return toolkit.response(data)


# warning: this endpoint is deprecated in favor of tag-specific json
# implemented by get_repository_tag_json
@app.route('/v1/repositories/<path:repository>/json', methods=['GET'])
@toolkit.parse_repository_name
@toolkit.requires_auth
@mirroring.source_lookup(stream=False, cache=True)
def get_repository_json(namespace, repository):
    json_path = store.repository_json_path(namespace, repository)
    headers = {}
    data = {'last_update': None,
            'docker_version': None,
            'docker_go_version': None,
            'arch': 'amd64',
            'os': 'linux',
            'kernel': None}
    try:
        # Note(dmp): unicode patch
        data = store.get_json(json_path)
    except exceptions.FileNotFoundError:
        if mirroring.is_mirror():
            # use code 404 to trigger the source_lookup decorator.
            # TODO(joffrey): make sure this doesn't break anything or have the
            # decorator rewrite the status code before sending
            return toolkit.response(data, code=404, headers=headers)
        # else we ignore the error, we'll serve the default json declared above
    return toolkit.response(data, headers=headers)


@app.route(
    '/v1/repositories/<path:repository>/tags/<tag>/json',
    methods=['GET'])
@toolkit.parse_repository_name
@toolkit.requires_auth
def get_repository_tag_json(namespace, repository, tag):
    json_path = store.repository_tag_json_path(namespace, repository, tag)
    data = {'last_update': None,
            'docker_version': None,
            'docker_go_version': None,
            'arch': 'amd64',
            'os': 'linux',
            'kernel': None}
    try:
        # Note(dmp): unicode patch
        data = store.get_json(json_path)
    except exceptions.FileNotFoundError:
        # We ignore the error, we'll serve the default json declared above
        pass
    return toolkit.response(data)


def create_tag_json(user_agent):
    props = {
        'last_update': int(time.mktime(datetime.datetime.utcnow().timetuple()))
    }
    ua = dict(RE_USER_AGENT.findall(user_agent))
    if 'docker' in ua:
        props['docker_version'] = ua['docker']
    if 'go' in ua:
        props['docker_go_version'] = ua['go']
    for k in ['arch', 'kernel', 'os']:
        if k in ua:
            props[k] = ua[k].lower()
    return json.dumps(props)


@app.route('/v1/repositories/<path:repository>/tags/<tag>',
           methods=['PUT'])
@toolkit.parse_repository_name
@toolkit.requires_auth
def put_tag(namespace, repository, tag):
    logger.debug("[put_tag] namespace={0}; repository={1}; tag={2}".format(
                 namespace, repository, tag))
    data = None
    try:
        # Note(dmp): unicode patch
        data = json.loads(flask.request.data.decode('utf8'))
    except ValueError:
        pass
    if not data or not isinstance(data, basestring):
        return toolkit.api_error('Invalid data')
    if not store.exists(store.image_json_path(data)):
        return toolkit.api_error('Image not found', 404)
    store.put_content(store.tag_path(namespace, repository, tag), data)
    sender = flask.current_app._get_current_object()
    signals.tag_created.send(sender, namespace=namespace,
                             repository=repository, tag=tag, value=data)
    # Write some meta-data about the repos
    ua = flask.request.headers.get('user-agent', '')
    data = create_tag_json(user_agent=ua)
    json_path = store.repository_tag_json_path(namespace, repository, tag)
    store.put_content(json_path, data)
    if tag == "latest":  # TODO(dustinlacewell) : deprecate this for v2
        json_path = store.repository_json_path(namespace, repository)
        store.put_content(json_path, data)
    return toolkit.response()


def delete_tag(namespace, repository, tag):
    logger.debug("[delete_tag] namespace={0}; repository={1}; tag={2}".format(
                 namespace, repository, tag))
    tag_path = store.tag_path(namespace, repository, tag)
    image = store.get_content(path=tag_path)
    store.remove(tag_path)
    store.remove(store.repository_tag_json_path(namespace, repository,
                                                tag))
    sender = flask.current_app._get_current_object()
    if tag == "latest":  # TODO(wking) : deprecate this for v2
        store.remove(store.repository_json_path(namespace, repository))
    signals.tag_deleted.send(
        sender, namespace=namespace, repository=repository, tag=tag,
        image=image)


@app.route('/v1/repositories/<path:repository>/tags/<tag>',
           methods=['DELETE'])
@toolkit.parse_repository_name
@toolkit.requires_auth
def _delete_tag(namespace, repository, tag):
    # XXX backends are inconsistent on this - some will throw, but not all
    try:
        delete_tag(namespace=namespace, repository=repository, tag=tag)
    except exceptions.FileNotFoundError:
        return toolkit.api_error('Tag not found: %s' % tag, 404)
    return toolkit.response()


def _import_repository(src_image, namespace, repository):
    """import a repository's tag from a given source index and place the
    information at the given target.
    """
    # strip tag from src_image if present
    src_tag = None
    if ':' in src_image:
        if '/' not in src_image[src_image.rfind(':') + 1:]:
            src_tag = src_image[src_image.rfind(':') + 1:]
            src_image = src_image[:src_image.rfind(':')]

    src_index, src_repository = _resolve_repository_name(src_image)
    headers = None

    # check if src_index starts with a scheme; mandatory for requests library
    if not src_index.startswith('http'):
        src_index = 'http://' + src_index

    tags_url = '{0}/v1/repositories/{1}/tags'.format(
        src_index,
        src_repository)
    tags_resp = requests.get(tags_url)
    images_resp = requests.get(
        '{0}/v1/repositories/{1}/images'.format(
            src_index,
            src_repository
        ),
        # this is required if we are authenticating against the public index
        # because we need the token returned in the header to retrieve images
        headers={'X-Docker-Token': 'true'}
    )

    if images_resp and tags_resp:
        # import images
        images = json.loads(images_resp.content)
        tags = json.loads(tags_resp.content)
        if src_index == toolkit.public_index_url():
            tags = _get_public_index_tags(tags_resp, images)

        if src_index == toolkit.public_index_url():
            # DockerHub requires a token even on unauthenticated requests
            token = images_resp.headers['x-docker-token']
            headers = {'Authorization': 'Token {0}'.format(token)}

        if src_tag is not None:
            tags = {src_tag: tags[src_tag]}
            # retrieve ancestry from the tag's parent image
            if src_index == toolkit.public_index_url():
                req_url = toolkit.public_cdn_url()
            else:
                req_url = src_index
            images = [{'id': image} for image in json.loads(
                requests.get(
                    '{0}/v1/images/{1}/ancestry'.format(
                        req_url,
                        tags[src_tag]),
                        headers=headers
                ).content
            )]

        if src_index == toolkit.public_index_url():
            image_list = []
            for image in images:
                image_list.append({'id': image['id']})
            images = image_list
        logger.debug('images={0}'.format(images))
        for image in images:
            logger.debug("Downloading image {0} from {1}".format(
                image['id'],
                src_index)
            )
            if src_index == toolkit.public_index_url():
                # docker images are stored on the CDN
                _import_image(toolkit.public_cdn_url(), image['id'], headers)
            else:
                _import_image(src_index, image['id'], headers)
        store.put_content(store.index_images_path(namespace, repository),
                          json.dumps(images))
        # import tags
        logger.debug('tags={0}'.format(tags))
        for tag, image in tags.items():
            logger.debug("Downloading tag {0} from {1}".format(tag, src_index))
            store.put_content(store.tag_path(namespace, repository, tag),
                              image)
    else:
        raise Exception("did not receive response from remote")


def _get_public_index_tags(tags_resp, images):
    tags = {}
    for tag in json.loads(tags_resp.content):
        # public index shortens the image ID on /tags, so we need the
        # fully-qualified name
        for image in images:
            if image['id'].startswith(tag['layer']):
                tag['layer'] = image['id']
            tags[tag['name']] = tag['layer']
    return tags


def _resolve_repository_name(image_name):
    """this code mimics the logic of the docker client as of commit
    dotcloud/docker@4a3b36f44309ff8e650be2cff74f3ec436353298
    see registry/registry.go#L117
    """
    logger.debug("[_resolve_repository_name] "
                 "image_name={0}".format(image_name))

    nameparts = image_name.split('/', 1)
    if len(nameparts) == 1 or \
            not '.' in nameparts[0] and \
            not ':' in nameparts[0] and  \
            nameparts[0] != 'localhost':
        # this is a docker index repository (e.g. samalba/hipache or ubuntu)
        _validate_repository_name(image_name)
        return toolkit.public_index_url(), image_name
    hostname = nameparts[0]
    repo_name = nameparts[1]
    if 'index.docker.io' in hostname:
        raise ValueError('Invalid repository name, try {0} '
                         'instead'.format(image_name))
    _validate_repository_name(repo_name)

    return hostname, repo_name


def _validate_repository_name(repository_name):
    """this code mimics the logic of the docker client as of commit
    dotcloud/docker@4a3b36f44309ff8e650be2cff74f3ec436353298
    see registry/registry.go#L92
    """
    logger.debug("[_validate_repository_name] "
                 "repository_name={0}".format(repository_name))
    nameParts = repository_name.split('/', 2)
    if len(nameParts) < 2:
        namespace = "library"
        name = nameParts[0]
    else:
        namespace = nameParts[0]
        name = nameParts[1]
    validNamespace = re.compile('^([a-z0-9_]{4,30})$')
    if not validNamespace.match(namespace):
        raise ValueError("Invalid namespace name ({0}), only [a-z0-9_] are "
                         "allowed, size between 4 and 30".format(namespace))
    validRepo = re.compile('^([a-z0-9-_.]+)$')
    if not validRepo.match(name):
        raise ValueError("Invalid repository name ({0}), only [a-z0-9-_.] are "
                         "allowed".format(name))


def _import_image(source, image, headers=None):
    # do nothing if the image already exists
    if store.exists(store.image_layer_path(image)):
        logger.debug('image {0} already exists, skipping'.format(image))
        return

    # import layers
    resp = requests.get('{0}/v1/images/{1}/layer'.format(source, image),
                        headers=headers)
    if resp:
        store.put_content(store.image_layer_path(image), resp.content)
    # import JSON
    resp = requests.get('{0}/v1/images/{1}/json'.format(source, image),
                        headers=headers)
    if resp:
        store.put_content(store.image_json_path(image), resp.content)
    # import ancestry
    resp = requests.get('{0}/v1/images/{1}/ancestry'.format(source, image),
                        headers=headers)
    if resp:
        store.put_content(store.image_ancestry_path(image), resp.content)


@app.route('/v1/repositories/<path:repository>/',
           methods=['DELETE', 'POST'])
@app.route('/v1/repositories/<path:repository>/tags',
           methods=['DELETE', 'POST'])
@toolkit.parse_repository_name
@toolkit.requires_auth
def delete_repository(namespace, repository):
    """Remove a repository from storage

    This endpoint exists in both the registry API [1] and the indexer
    API [2], but has the same semantics in each instance.  It's in the
    tags module (instead of the index module which handles most
    repository tasks) because it should be available regardless of
    whether the rest of the index-module endpoints are enabled via the
    'standalone' config setting.

    [1]: http://docs.docker.io/en/latest/reference/api/registry_api/#delete--v1-repositories-%28namespace%29-%28repository%29- # nopep8
    [2]: http://docs.docker.io/en/latest/reference/api/index_api/#delete--v1-repositories-%28namespace%29-%28repo_name%29- # nopep8
    """
    logger.debug("[delete_repository] namespace={0}; repository={1}".format(
                 namespace, repository))
    try:
        for tag_name, tag_content in get_tags(
                namespace=namespace, repository=repository).items():
            delete_tag(
                namespace=namespace, repository=repository, tag=tag_name)
        # TODO(wking): remove images, but may need refcounting
        store.remove(store.repository_path(
            namespace=namespace, repository=repository))
    except exceptions.FileNotFoundError:
        return toolkit.api_error('Repository not found', 404)
    else:
        try:
            for tag_name, tag_content in get_tags(
                    namespace=namespace, repository=repository):
                delete_tag(
                    namespace=namespace, repository=repository, tag=tag_name)
            # TODO(wking): remove images, but may need refcounting
            store.remove(store.repository_path(
                namespace=namespace, repository=repository))
        except exceptions.FileNotFoundError:
            return toolkit.api_error('Repository not found', 404)
        else:
            sender = flask.current_app._get_current_object()
            signals.repository_deleted.send(
                sender, namespace=namespace, repository=repository)
    return toolkit.response()
