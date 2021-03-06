# -*- coding: utf-8 -*-
###########################################################################
# Copyright (c), The AiiDA team. All rights reserved.                     #
# This file is part of the AiiDA code.                                    #
#                                                                         #
# The code is hosted on GitHub at https://github.com/aiidateam/aiida-core #
# For further information on the license, see the LICENSE.txt file        #
# For further information please visit http://www.aiida.net               #
###########################################################################
""" Utility functions for export of AiiDA entities """
# pylint: disable=too-many-locals,too-many-branches,too-many-nested-blocks
from enum import Enum
import warnings

from aiida.orm import QueryBuilder, ProcessNode
from aiida.common.log import AIIDA_LOGGER, LOG_LEVEL_REPORT, override_log_formatter
from aiida.common.warnings import AiidaDeprecationWarning

from aiida.tools.importexport.common import exceptions
from aiida.tools.importexport.common.config import (
    file_fields_to_model_fields, entity_names_to_entities, get_all_fields_info
)

EXPORT_LOGGER = AIIDA_LOGGER.getChild('export')


class ExportFileFormat(str, Enum):
    """Export file formats"""
    ZIP = 'zip'
    TAR_GZIPPED = 'tar.gz'


def fill_in_query(partial_query, originating_entity_str, current_entity_str, tag_suffixes=None, entity_separator='_'):
    """
    This function recursively constructs QueryBuilder queries that are needed
    for the SQLA export function. To manage to construct such queries, the
    relationship dictionary is consulted (which shows how to reference
    different AiiDA entities in QueryBuilder.
    To find the dependencies of the relationships of the exported data, the
    get_all_fields_info (which described the exported schema and its
    dependencies) is consulted.
    """
    if not tag_suffixes:
        tag_suffixes = []

    relationship_dic = {
        'Node': {
            'Computer': 'with_computer',
            'Group': 'with_group',
            'User': 'with_user',
            'Log': 'with_log',
            'Comment': 'with_comment'
        },
        'Group': {
            'Node': 'with_node'
        },
        'Computer': {
            'Node': 'with_node'
        },
        'User': {
            'Node': 'with_node',
            'Group': 'with_group',
            'Comment': 'with_comment',
        },
        'Log': {
            'Node': 'with_node'
        },
        'Comment': {
            'Node': 'with_node',
            'User': 'with_user'
        }
    }

    all_fields_info, _ = get_all_fields_info()

    entity_prop = all_fields_info[current_entity_str].keys()

    project_cols = ['id']
    for prop in entity_prop:
        nprop = prop
        if current_entity_str in file_fields_to_model_fields:
            if prop in file_fields_to_model_fields[current_entity_str]:
                nprop = file_fields_to_model_fields[current_entity_str][prop]

        project_cols.append(nprop)

    # Here we should reference the entity of the main query
    current_entity_mod = entity_names_to_entities[current_entity_str]

    rel_string = relationship_dic[current_entity_str][originating_entity_str]
    mydict = {rel_string: entity_separator.join(tag_suffixes)}

    partial_query.append(
        current_entity_mod,
        tag=entity_separator.join(tag_suffixes) + entity_separator + current_entity_str,
        project=project_cols,
        outerjoin=True,
        **mydict
    )

    # prepare the recursion for the referenced entities
    foreign_fields = {
        k: v
        for k, v in all_fields_info[current_entity_str].items()
        # all_fields_info[model_name].items()
        if 'requires' in v
    }

    new_tag_suffixes = tag_suffixes + [current_entity_str]
    for value in foreign_fields.values():
        ref_model_name = value['requires']
        fill_in_query(partial_query, current_entity_str, ref_model_name, new_tag_suffixes)


def check_licenses(node_licenses, allowed_licenses, forbidden_licenses):
    """Check licenses"""
    from aiida.common.exceptions import LicensingException
    from inspect import isfunction

    for pk, license_ in node_licenses:
        if allowed_licenses is not None:
            try:
                if isfunction(allowed_licenses):
                    try:
                        if not allowed_licenses(license_):
                            raise LicensingException
                    except Exception:
                        raise LicensingException
                else:
                    if license_ not in allowed_licenses:
                        raise LicensingException
            except LicensingException:
                raise LicensingException(
                    f'Node {pk} is licensed under {license_} license, which is not in the list of allowed licenses'
                )
        if forbidden_licenses is not None:
            try:
                if isfunction(forbidden_licenses):
                    try:
                        if forbidden_licenses(license_):
                            raise LicensingException
                    except Exception:
                        raise LicensingException
                else:
                    if license_ in forbidden_licenses:
                        raise LicensingException
            except LicensingException:
                raise LicensingException(
                    f'Node {pk} is licensed under {license_} license, which is in the list of forbidden licenses'
                )


def serialize_field(data, track_conversion=False):
    """
    Serialize a single field.

    :todo: Generalize such that it the proper function is selected also during
        import
    """
    import datetime
    import pytz
    from uuid import UUID

    if isinstance(data, dict):
        ret_data = {}
        if track_conversion:
            ret_conversion = {}
            for key, value in data.items():
                ret_data[key], ret_conversion[key] = serialize_field(data=value, track_conversion=track_conversion)
        else:
            for key, value in data.items():
                ret_data[key] = serialize_field(data=value, track_conversion=track_conversion)
    elif isinstance(data, (list, tuple)):
        ret_data = []
        if track_conversion:
            ret_conversion = []
            for value in data:
                this_data, this_conversion = serialize_field(data=value, track_conversion=track_conversion)
                ret_data.append(this_data)
                ret_conversion.append(this_conversion)
        else:
            for value in data:
                ret_data.append(serialize_field(data=value, track_conversion=track_conversion))
    elif isinstance(data, datetime.datetime):
        # Note: requires timezone-aware objects!
        ret_data = data.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')
        ret_conversion = 'date'
    elif isinstance(data, UUID):
        ret_data = str(data)
        ret_conversion = None
    else:
        ret_data = data
        ret_conversion = None

    if track_conversion:
        return (ret_data, ret_conversion)
    return ret_data


def serialize_dict(datadict, remove_fields=None, rename_fields=None, track_conversion=False):
    """
    Serialize the dict using the serialize_field function to serialize
    each field.

    :param remove_fields: a list of strings.
      If a field with key inside the remove_fields list is found,
      it is removed from the dict.

      This is only used at level-0, no removal
      is possible at deeper levels.

    :param rename_fields: a dictionary in the format
      ``{"oldname": "newname"}``.

      If the "oldname" key is found, it is replaced with the
      "newname" string in the output dictionary.

      This is only used at level-0, no renaming
      is possible at deeper levels.
    :param track_conversion: if True, a tuple is returned, where the first
      element is the serialized dictionary, and the second element is a
      dictionary with the information on the serialized fields.
    """
    ret_dict = {}
    conversions = {}

    if not remove_fields:
        remove_fields = []

    if not rename_fields:
        rename_fields = {}

    for key, value in datadict.items():
        if key not in remove_fields:
            # rename_fields.get(key,key): use the replacement if found in rename_fields,
            # otherwise use 'key' as the default value.
            if track_conversion:
                (ret_dict[rename_fields.get(key, key)],
                 conversions[rename_fields.get(key,
                                               key)]) = serialize_field(data=value, track_conversion=track_conversion)
            else:
                ret_dict[rename_fields.get(key, key)] = serialize_field(data=value, track_conversion=track_conversion)

    if track_conversion:
        return (ret_dict, conversions)
    return ret_dict


def check_process_nodes_sealed(nodes):
    """Check ``ProcessNode`` s are sealed
    Only sealed ``ProcessNode`` s may be exported.

    :param nodes: :py:class:`~aiida.orm.nodes.process.process.ProcessNode` s to be checked. Should be their PK(s).
    :type nodes: list, int

    :raises `~aiida.tools.importexport.common.exceptions.ExportValidationError`:
        if a ``ProcessNode`` is not sealed or `nodes` is not a `list`, `set`, or `int`.
    """
    if not nodes:
        return

    # Check `nodes` type, and if necessary change to set
    if isinstance(nodes, set):
        pass
    elif isinstance(nodes, list):
        nodes = set(nodes)
    elif isinstance(nodes, int):
        nodes = set([nodes])
    else:
        raise exceptions.ExportValidationError('nodes must be either an int or set/list of ints')

    filters = {'id': {'in': nodes}, 'attributes.sealed': True}
    sealed_nodes = set(QueryBuilder().append(ProcessNode, filters=filters, project=['id']).all(flat=True))

    if sealed_nodes != nodes:
        raise exceptions.ExportValidationError(
            'All ProcessNodes must be sealed before they can be exported. '
            'Node(s) with PK(s): {} is/are not sealed.'.format(', '.join(str(pk) for pk in nodes - sealed_nodes))
        )


@override_log_formatter('%(message)s')
def summary(file_format, outfile, **kwargs):
    """Print summary for export"""
    from tabulate import tabulate
    from aiida.tools.importexport.common.config import EXPORT_VERSION

    parameters = [['Archive', outfile], ['Format', file_format], ['Export version', EXPORT_VERSION]]

    result = f"\n{tabulate(parameters, headers=['EXPORT', ''])}"

    include_comments = kwargs.get('include_comments', True)
    include_logs = kwargs.get('include_logs', True)
    input_forward = kwargs.get('input_forward', False)
    create_reversed = kwargs.get('create_reversed', True)
    return_reversed = kwargs.get('return_reversed', False)
    call_reversed = kwargs.get('call_reversed', False)

    inclusions = [['Include Comments', include_comments], ['Include Logs', include_logs]]
    result += f"\n\n{tabulate(inclusions, headers=['Inclusion rules', ''])}"

    traversal_rules = [['Follow INPUT Links forwards',
                        input_forward], ['Follow CREATE Links backwards', create_reversed],
                       ['Follow RETURN Links backwards', return_reversed],
                       ['Follow CALL Links backwards', call_reversed]]
    result += f"\n\n{tabulate(traversal_rules, headers=['Traversal rules', ''])}\n"

    EXPORT_LOGGER.log(msg=result, level=LOG_LEVEL_REPORT)


def deprecated_parameters(old, new):
    """Handle deprecated parameter (where it is replaced with another)

    :param old: The old, deprecated parameter as a dict with keys "name" and "value"
    :type old: dict

    :param new: The new parameter as a dict with keys "name" and "value"
    :type new: dict

    :return: New parameter's value (if not defined, then old parameter's value)
    """
    if old.get('value', None) is not None:
        if new.get('value', None) is not None:
            message = f"`{old['name']}` is deprecated, the supplied `{new['name']}` input will be used"
        else:
            message = f"`{old['name']}` is deprecated, please use `{new['name']}` instead"
            new['value'] = old['value']
        warnings.warn(message, AiidaDeprecationWarning)  # pylint: disable=no-member

    return new['value']
