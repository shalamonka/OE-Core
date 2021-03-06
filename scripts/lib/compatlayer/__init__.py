# Yocto Project compatibility layer tool
#
# Copyright (C) 2017 Intel Corporation
# Released under the MIT license (see COPYING.MIT)

import os
from enum import Enum

class LayerType(Enum):
    BSP = 0
    DISTRO = 1
    SOFTWARE = 2
    ERROR_NO_LAYER_CONF = 98
    ERROR_BSP_DISTRO = 99

def _get_configurations(path):
    configs = []

    for f in os.listdir(path):
        file_path = os.path.join(path, f)
        if os.path.isfile(file_path) and f.endswith('.conf'):
            configs.append(f[:-5]) # strip .conf
    return configs

def _get_layer_collections(layer_path, lconf=None, data=None):
    import bb.parse
    import bb.data

    if lconf is None:
        lconf = os.path.join(layer_path, 'conf', 'layer.conf')

    if data is None:
        ldata = bb.data.init()
        bb.parse.init_parser(ldata)
    else:
        ldata = data.createCopy()

    ldata.setVar('LAYERDIR', layer_path)
    try:
        ldata = bb.parse.handle(lconf, ldata, include=True)
    except BaseException as exc:
        raise LayerError(exc)
    ldata.expandVarref('LAYERDIR')

    collections = (ldata.getVar('BBFILE_COLLECTIONS', True) or '').split()
    if not collections:
        name = os.path.basename(layer_path)
        collections = [name]

    collections = {c: {} for c in collections}
    for name in collections:
        priority = ldata.getVar('BBFILE_PRIORITY_%s' % name, True)
        pattern = ldata.getVar('BBFILE_PATTERN_%s' % name, True)
        depends = ldata.getVar('LAYERDEPENDS_%s' % name, True)
        collections[name]['priority'] = priority
        collections[name]['pattern'] = pattern
        collections[name]['depends'] = depends

    return collections

def _detect_layer(layer_path):
    """
        Scans layer directory to detect what type of layer
        is BSP, Distro or Software.

        Returns a dictionary with layer name, type and path.
    """

    layer = {}
    layer_name = os.path.basename(layer_path)

    layer['name'] = layer_name
    layer['path'] = layer_path
    layer['conf'] = {}

    if not os.path.isfile(os.path.join(layer_path, 'conf', 'layer.conf')):
        layer['type'] = LayerType.ERROR_NO_LAYER_CONF
        return layer

    machine_conf = os.path.join(layer_path, 'conf', 'machine')
    distro_conf = os.path.join(layer_path, 'conf', 'distro')

    is_bsp = False
    is_distro = False

    if os.path.isdir(machine_conf):
        machines = _get_configurations(machine_conf)
        if machines:
            is_bsp = True

    if os.path.isdir(distro_conf):
        distros = _get_configurations(distro_conf)
        if distros:
            is_distro = True

    if is_bsp and is_distro:
        layer['type'] = LayerType.ERROR_BSP_DISTRO
    elif is_bsp:
        layer['type'] = LayerType.BSP
        layer['conf']['machines'] = machines
    elif is_distro:
        layer['type'] = LayerType.DISTRO
        layer['conf']['distros'] = distros
    else:
        layer['type'] = LayerType.SOFTWARE

    layer['collections'] = _get_layer_collections(layer['path'])

    return layer

def detect_layers(layer_directories, no_auto):
    layers = []

    for directory in layer_directories:
        directory = os.path.realpath(directory)
        if directory[-1] == '/':
            directory = directory[0:-1]

        if no_auto:
            conf_dir = os.path.join(directory, 'conf')
            if os.path.isdir(conf_dir):
                layer = _detect_layer(directory)
                if layer:
                    layers.append(layer)
        else:
            for root, dirs, files in os.walk(directory):
                dir_name = os.path.basename(root)
                conf_dir = os.path.join(root, 'conf')
                if os.path.isdir(conf_dir):
                    layer = _detect_layer(root)
                    if layer:
                        layers.append(layer)

    return layers

def _find_layer_depends(depend, layers):
    for layer in layers:
        for collection in layer['collections']:
            if depend == collection:
                return layer
    return None

def add_layer(bblayersconf, layer, layers, logger):
    logger.info('Adding layer %s' % layer['name'])

    for collection in layer['collections']:
        depends = layer['collections'][collection]['depends']
        if not depends:
            continue

        for depend in depends.split():
            # core (oe-core) is suppose to be provided
            if depend == 'core':
                continue

            layer_depend = _find_layer_depends(depend, layers)
            if not layer_depend:
                logger.error('Layer %s depends on %s and isn\'t found.' % \
                        (layer['name'], depend))
                return False

            logger.info('Adding layer dependency %s' % layer_depend['name'])
            with open(bblayersconf, 'a+') as f:
                f.write("\nBBLAYERS += \"%s\"\n" % layer_depend['path'])

    with open(bblayersconf, 'a+') as f:
        f.write("\nBBLAYERS += \"%s\"\n" % layer['path'])

    return True

def get_signatures(builddir, failsafe=False):
    import subprocess
    import re

    # some recipes needs to be excluded like meta-world-pkgdata
    # because a layer can add recipes to a world build so signature
    # will be change
    exclude_recipes = ('meta-world-pkgdata',)

    sigs = {}

    cmd = 'bitbake '
    if failsafe:
        cmd += '-k '
    cmd += '-S none world'
    p = subprocess.Popen(cmd, shell=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output, _ = p.communicate()
    if p.returncode:
        msg = "Generating signatures failed. This might be due to some parse error and/or general layer incompatibilities.\nCommand: %s\nOutput:\n%s" % (cmd, output.decode('utf-8'))
        raise RuntimeError(msg)
    sigs_file = os.path.join(builddir, 'locked-sigs.inc')

    sig_regex = re.compile("^(?P<task>.*:.*):(?P<hash>.*) .$")
    with open(sigs_file, 'r') as f:
        for line in f.readlines():
            line = line.strip()
            s = sig_regex.match(line)
            if s:
                exclude = False
                for er in exclude_recipes:
                    (recipe, task) = s.group('task').split(':')
                    if er == recipe:
                        exclude = True
                        break
                if exclude:
                    continue

                sigs[s.group('task')] = s.group('hash')

    if not sigs:
        raise RuntimeError('Can\'t load signatures from %s' % sigs_file)

    return sigs
