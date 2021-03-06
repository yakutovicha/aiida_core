# -*- coding: utf-8 -*-
###########################################################################
# Copyright (c), The AiiDA team. All rights reserved.                     #
# This file is part of the AiiDA code.                                    #
#                                                                         #
# The code is hosted on GitHub at https://github.com/aiidateam/aiida-core #
# For further information on the license, see the LICENSE.txt file        #
# For further information please visit http://www.aiida.net               #
###########################################################################
"""Tests for the :mod:`aiida.engine.daemon.execmanager` module."""
import io
import os
import pathlib
import pytest

from aiida import orm
from aiida.engine.daemon import execmanager
from aiida.common.datastructures import CalcInfo, CodeInfo
from aiida.transports.plugins.local import LocalTransport


@pytest.mark.usefixtures('clear_database_before_test')
def test_retrieve_files_from_list(tmp_path_factory, generate_calculation_node):
    """Test the `retrieve_files_from_list` function."""
    node = generate_calculation_node()

    retrieve_list = [
        'file_a.txt',
        ('sub/folder', 'sub/folder', 0),
    ]

    source = tmp_path_factory.mktemp('source')
    target = tmp_path_factory.mktemp('target')

    content_a = b'content_a'
    content_b = b'content_b'

    with open(str(source / 'file_a.txt'), 'wb') as handle:
        handle.write(content_a)
        handle.flush()

    os.makedirs(str(source / 'sub' / 'folder'))

    with open(str(source / 'sub' / 'folder' / 'file_b.txt'), 'wb') as handle:
        handle.write(content_b)
        handle.flush()

    with LocalTransport() as transport:
        transport.chdir(str(source))
        execmanager.retrieve_files_from_list(node, transport, str(target), retrieve_list)

    assert sorted(os.listdir(str(target))) == sorted(['file_a.txt', 'sub'])
    assert os.listdir(str(target / 'sub')) == ['folder']
    assert os.listdir(str(target / 'sub' / 'folder')) == ['file_b.txt']

    with open(str(target / 'sub' / 'folder' / 'file_b.txt'), 'rb') as handle:
        assert handle.read() == content_b

    with open(str(target / 'file_a.txt'), 'rb') as handle:
        assert handle.read() == content_a


@pytest.mark.usefixtures('clear_database_before_test')
def test_upload_local_copy_list(fixture_sandbox, aiida_localhost, aiida_local_code_factory):
    """Test the ``local_copy_list`` functionality in ``upload_calculation``.

    Specifically, verify that files in the ``local_copy_list`` do not end up in the repository of the node.
    """
    from aiida.orm import CalcJobNode, SinglefileData

    inputs = {
        'file_a': SinglefileData(io.BytesIO(b'content_a')).store(),
        'file_b': SinglefileData(io.BytesIO(b'content_b')).store(),
    }

    node = CalcJobNode(computer=aiida_localhost)
    node.store()

    code = aiida_local_code_factory('arithmetic.add', '/bin/bash').store()
    code_info = CodeInfo()
    code_info.code_uuid = code.uuid

    calc_info = CalcInfo()
    calc_info.uuid = node.uuid
    calc_info.codes_info = [code_info]
    calc_info.local_copy_list = [
        (inputs['file_a'].uuid, inputs['file_a'].filename, './files/file_a'),
        (inputs['file_a'].uuid, inputs['file_a'].filename, './files/file_b'),
    ]

    with LocalTransport() as transport:
        execmanager.upload_calculation(node, transport, calc_info, fixture_sandbox)

    assert node.list_object_names() == []


def test_upload_calculation(aiida_localhost, aiida_local_code_factory, fixture_sandbox, tmp_path):
    """Test the `upload_calculation` function, and specifically the copy lists."""

    # FolderData: needs to be stored when is checked by upload_calculation
    folder_node = orm.FolderData()
    folder_node.put_object_from_filelike(io.StringIO('dummy_content_1'), 'file_1.txt')
    folder_node.store()

    # RemoteData: upload_calculation will not check if it is stored (should it?)
    remote_node = orm.RemoteData(computer=aiida_localhost, remote_path=str(tmp_path))

    source_path2 = str(tmp_path / 'file_2.txt')
    with open(source_path2, 'w') as handle:
        handle.write('dummy_content_2')
        handle.flush()

    source_path3 = str(tmp_path / 'file_3.txt')
    with open(source_path3, 'w') as handle:
        handle.write('dummy_content_3')
        handle.flush()

    # CodeInfo: needs to be set up as normal for any CalcJob
    code_node = aiida_local_code_factory('arithmetic.add', '/bin/bash')
    code_info = CodeInfo()
    code_info.code_uuid = code_node.uuid

    # CalcInfo: besides normal setups, we also need to manually set calc_info.uuid
    # as we are skipping the step of the engine were this happens.
    calc_node = orm.CalcJobNode(computer=aiida_localhost).store()
    calc_info = CalcInfo()
    calc_info.uuid = calc_node.uuid
    calc_info.codes_info = [code_info]
    calc_info.local_copy_list = [(folder_node.uuid, 'file_1.txt', 'local_file/file_1.txt')]
    calc_info.remote_copy_list = [(remote_node.computer.uuid, source_path2, 'remote_file/file_2.txt')]
    calc_info.remote_symlink_list = [(remote_node.computer.uuid, source_path3, 'symlink_file/file_3.sym')]

    # We need to manually open a transport and pass it to upload_calculation, together
    # with a pre-set up sandbox folder (in this case it can be empty since we are mostly
    # checking through the copy lists)
    with LocalTransport() as transport:
        execmanager.upload_calculation(calc_node, transport, calc_info, fixture_sandbox)

    calc_folder_path = pathlib.Path(calc_node.get_remote_workdir())

    # Although in principle not necessary, the checks are performed increasingly from the
    # existence of folders to content of contained files so it will be easier to identify
    # which part of the copying is broken.
    list_of_folders = [
        calc_folder_path / 'local_file',
        calc_folder_path / 'remote_file',
        calc_folder_path / 'symlink_file',
    ]
    assert sorted(list(calc_folder_path.iterdir())) == sorted(list_of_folders)

    assert list((calc_folder_path / 'local_file').iterdir()) == [calc_folder_path / 'local_file/file_1.txt']
    assert list((calc_folder_path / 'remote_file').iterdir()) == [calc_folder_path / 'remote_file/file_2.txt']
    assert list((calc_folder_path / 'symlink_file').iterdir()) == [calc_folder_path / 'symlink_file/file_3.sym']

    full_file_path = str(calc_folder_path / 'local_file/file_1.txt')
    with open(full_file_path, 'r') as handle:
        assert handle.read() == 'dummy_content_1'

    full_file_path = str(calc_folder_path / 'remote_file/file_2.txt')
    with open(full_file_path, 'r') as handle:
        assert handle.read() == 'dummy_content_2'

    full_file_path = str(calc_folder_path / 'symlink_file/file_3.sym')
    with open(full_file_path, 'r') as handle:
        assert handle.read() == 'dummy_content_3'
