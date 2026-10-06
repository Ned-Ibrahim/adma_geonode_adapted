"""Pages for the ADAPT share, read live from the mount and addressed by path.

See filemanager.adapt_share for how paths are named and checked, and
filemanager.permissions for who may see what.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect

from . import adapt_records, adapt_share, permissions


@login_required
def adapt_folder(request, rel=''):
    raise Http404('Not built yet')


@login_required
def adapt_open(request, rel):
    """Open file `rel` on the share: make its record if it has none yet, then show its file page.

    Anything the user may not read, or that is not a file ADMA shows, is a 404,
    so a refused path looks exactly like a missing one.
    """
    try:
        rel = adapt_share.normalize(rel)
        if not permissions.can_read_path(request.user, rel, is_dir=False):
            raise Http404('File not found')
        record = adapt_records.record_for(rel)
    except adapt_share.AdaptPathError:
        raise Http404('File not found')
    return redirect('filemanager:file_detail', file_id=record.id)
