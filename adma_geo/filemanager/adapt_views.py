"""Pages for the ADAPT share, read live from the mount and addressed by path.

See filemanager.adapt_share for how paths are named and checked, and
filemanager.permissions for who may see what.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404


@login_required
def adapt_folder(request, rel=''):
    raise Http404('Not built yet')


@login_required
def adapt_open(request, rel):
    raise Http404('Not built yet')
