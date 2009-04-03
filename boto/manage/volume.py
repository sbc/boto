# Copyright (c) 2006-2009 Mitch Garnaat http://garnaat.org/
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish, dis-
# tribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the fol-
# lowing conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABIL-
# ITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT
# SHALL THE AUTHOR BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, 
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

from __future__ import with_statement
from boto.sdb.db.model import Model
from boto.sdb.db.property import *
from boto.manage.server import Server
from boto.manage import propget
import boto.ec2
import time, traceback
from contextlib import closing
import dateutil.parser

class CommandLineGetter(object):
    
    def get_region(self, params):
        if not params.get('region', None):
            prop = self.cls.find_property('region_name')
            params['region'] = propget.get(prop, choices=boto.ec2.regions)

    def get_zone(self, params):
        if not params.get('zone', None):
            prop = StringProperty(name='zone', verbose_name='EC2 Availability Zone',
                                  choices=self.ec2.get_all_zones)
            params['zone'] = propget.get(prop)
            
    def get_name(self, params):
        if not params.get('name', None):
            prop = self.cls.find_property('name')
            params['name'] = propget.get(prop)

    def get_size(self, params):
        if not params.get('size', None):
            prop = IntegerProperty(name='size', verbose_name='Size (GB)')
            params['size'] = propget.get(prop)

    def get_mount_point(self, params):
        if not params.get('mount_point', None):
            prop = self.cls.find_property('mount_point')
            params['mount_point'] = propget.get(prop)

    def get_device(self, params):
        if not params.get('device', None):
            prop = self.cls.find_property('device')
            params['device'] = propget.get(prop)

    def get(self, cls, params):
        self.cls = cls
        self.get_region(params)
        self.ec2 = params['region'].connect()
        self.get_zone(params)
        self.get_name(params)
        self.get_size(params)
        self.get_mount_point(params)
        self.get_device(params)

class Volume(Model):

    name = StringProperty(required=True, unique=True, verbose_name='Name')
    region_name = StringProperty(required=True, verbose_name='EC2 Region')
    mount_point = StringProperty(verbose_name='Mount Point')
    device = StringProperty(verbose_name="Device Name", default='/dev/sdp')
    volume_id = StringProperty(required=True)
    past_volume_ids = ListProperty(item_type=str)
    server = ReferenceProperty(Server, collection_name='volumes',
                               verbose_name='Server Attached To')
    volume_state = CalculatedProperty(verbose_name="Volume State",
                                      calculated_type=str, use_method=True)
    attachment_state = CalculatedProperty(verbose_name="Attachment State",
                                          calculated_type=str, use_method=True)
    size = CalculatedProperty(verbose_name="Size (GB)",
                              calculated_type=int, use_method=True)

    @classmethod
    def create(cls, **params):
        getter = CommandLineGetter()
        getter.get(cls, params)
        region = params.get('region')
        ec2 = region.connect()
        zone = params.get('zone')
        size = params.get('size')
        ebs_volume = ec2.create_volume(size, zone.name)
        v = cls()
        v.ec2 = ec2
        v.volume_id = ebs_volume.id
        v.name = params.get('name')
        v.mount_point = params.get('mount_point')
        v.device = params.get('device')
        v.region_name = region.name
        v.put()
        return v

    @classmethod
    def create_from_volume_id(cls, region_name, volume_id, name):
        vol = None
        ec2 = boto.ec2.connect_to_region(region_name)
        rs = ec2.get_all_volumes([volume_id])
        if len(rs) == 1:
            v = rs[0]
            vol = cls()
            vol.volume_id = v.id
            vol.name = name
            vol.region_name = v.region.name
            vol.put()
        return vol
    
    def get_ec2_connection(self):
        if self.server:
            return self.server.ec2
        if not hasattr(self, 'ec2') or self.ec2 == None:
            self.ec2 = boto.ec2.connect_to_region(self.region_name)
        return self.ec2

    def _volume_state(self):
        ec2 = self.get_ec2_connection()
        rs = ec2.get_all_volumes([self.volume_id])
        return rs[0].volume_state()

    def _attachment_state(self):
        ec2 = self.get_ec2_connection()
        rs = ec2.get_all_volumes([self.volume_id])
        return rs[0].attachment_state()

    def _size(self):
        if not hasattr(self, '__size'):
            ec2 = self.get_ec2_connection()
            rs = ec2.get_all_volumes([self.volume_id])
            self.__size = rs[0].size
        return self.__size

    def install_xfs(self):
        if self.server:
            self.server.install('xfsprogs xfsdump')

    def get_snapshots(self):
        """
        Returns a list of all completed snapshots for this volume ID.
        """
        ec2 = self.get_ec2_connection()
        rs = ec2.get_all_snapshots()
        snaps = []
        for snapshot in rs:
            if snapshot.volume_id == self.volume_id:
                if snapshot.progress == '100%':
                    snapshot.date = dateutil.parser.parse(snapshot.start_time)
                    snapshot.keep = True
                    snaps.append(snapshot)
        return snaps

    def attach(self, server=None):
        if self.attachment_state == 'attached':
            print 'already attached'
            return None
        if server:
            self.server = server
            self.put()
        ec2 = self.get_ec2_connection()
        ec2.attach_volume(self.volume_id, self.server.instance_id, self.device)

    def detach(self, force=False):
        state = self.attachment_state
        if state == 'available' or state == None or state == 'detaching':
            print 'already detached'
            return None
        ec2 = self.get_ec2_connection()
        ec2.detach_volume(self.volume_id, self.server.instance_id, self.device, force)
        self.server = None
        self.put()

    def checkfs(self, use_cmd=None):
        if self.server == None:
            raise ValueError, 'server attribute must be set to run this command'
        # detemine state of file system on volume, only works if attached
        if use_cmd:
            cmd = use_cmd
        else:
            cmd = self.server.get_cmdshell()
        status = cmd.run('xfs_check %s' % self.device)
        if not use_cmd:
            cmd.close()
        if status[1].startswith('bad superblock magic number 0'):
            return False
        return True

    def wait(self):
        if self.server == None:
            raise ValueError, 'server attribute must be set to run this command'
        with closing(self.server.get_cmdshell()) as cmd:
            # wait for the volume device to appear
            cmd = self.server.get_cmdshell()
            while not cmd.exists(self.device):
                boto.log.info('%s still does not exist, waiting 10 seconds' % self.device)
                time.sleep(10)

    def format(self):
        if self.server == None:
            raise ValueError, 'server attribute must be set to run this command'
        status = None
        with closing(self.server.get_cmdshell()) as cmd:
            if not self.checkfs(cmd):
                boto.log.info('make_fs...')
                status = cmd.run('mkfs -t xfs %s' % self.device)
        return status

    def mount(self):
        if self.server == None:
            raise ValueError, 'server attribute must be set to run this command'
        boto.log.info('handle_mount_point')
        with closing(self.server.get_cmdshell()) as cmd:
            cmd = self.server.get_cmdshell()
            if not cmd.isdir(self.mount_point):
                boto.log.info('making directory')
                # mount directory doesn't exist so create it
                cmd.run("mkdir %s" % self.mount_point)
            else:
                boto.log.info('directory exists already')
                status = cmd.run('mount -l')
                lines = status[1].split('\n')
                for line in lines:
                    t = line.split()
                    if t and t[2] == self.mount_point:
                        # something is already mounted at the mount point
                        # unmount that and mount it as /tmp
                        if t[0] != self.device:
                            cmd.run('umount %s' % self.mount_point)
                            cmd.run('mount %s /tmp' % t[0])
                            cmd.run('chmod 777 /tmp')
                            break
            # Mount up our new EBS volume onto mount_point
            cmd.run("mount %s %s" % (self.device, self.mount_point))
            cmd.run('xfs_growfs %s' % self.mount_point)

    def make_ready(self, server):
        self.server = server
        self.put()
        self.install_xfs()
        self.attach()
        self.wait()
        self.format()
        self.mount()

    def freeze(self):
        if self.server:
            return self.server.run("/usr/sbin/xfs_freeze -f %s" % self.mount_point)

    def unfreeze(self):
        if self.server:
            return self.server.run("/usr/sbin/xfs_freeze -u %s" % self.mount_point)

    def snapshot(self):
        # if this volume is attached to a server
        # we need to freeze the XFS file system
        try:
            status = self.freeze(keep_alive=True)
            print status[1]
            snapshot = self.server.ec2.create_snapshot(self.volume_id)
            boto.log.info('Snapshot of Volume %s created: %s' %  (self.name, snapshot))
        except Exception, e:
            boto.log.info('Snapshot error')
            boto.log.info(traceback.format_exc())
        finally:
            status = self.unfreeze()
            return status

    def trim_snapshots(self, keep_recent=4, keep_monthly=2, delete=True):
        """
        Trim the number of snapshots for this volume.  This method always
        keeps the oldest snapshot.  It then uses the parameters passed in
        to determine how many others should be kept.

        The basic approach is to first grab a list of all snapshots
        related to this volume, let's call this S.  That list is
        returned from AWS sorted by date so the last item in the list
        will be the newest snapshot.  This list is reversed, giving us
        a new list R.  We then trim R by ignoring the oldest snapshot
        (which is never deleted) and the N most recent snapshots where
        N is defined by the value of the keep_recent parameter.  This
        gives us yet another list called T.  We then take the first
        element of T (the oldest usable snapshot) and we determine the
        month for that snapshot by looking at it's timestamp.  We then
        collect all adjoining snapshots in T that have the same month
        associated with them and produce a list of snapshots from that
        month called M.  The task now is to choose K snapshots in this
        list of monthlies that we will keep.  The value of K is based
        on the parameter keep_monthly.  All other snapshots in the
        list M will be deleted.  To determine which snapshots we keep,
        we compute an interval value like this:

            I = int((len(M) / float(K)) + 0.5)

        We then need to keep every Ith snapshot in the list M.  We
        determine this by computing ordinal modulus I (integer
        remainder). If this value is zero (no remainder) then we keep
        the snapshot in that ordinal position.  If not, it is deleted.

        We then proceed to the next value of M until we have exhausted
        the list of trimmed list snapshots, T.

        """
        snaps = self.get_snapshots()
        snaps.reverse()
        num_snaps = len(snaps)
        # if number of snaps is less than the number of current snaps we want
        # to keep plus the oldest snap which we always keep, do nothing
        if keep_recent+1 >= num_snaps:
            return snaps
        end = len(snaps) - 2
        i = keep_recent
        while i < end:
            current = (snaps[i].date.month, snaps.i.date.year)
            l = [s for s in snaps[i:end] if (s.date.month, s.date.year) == current]
            if len(l) > keep_monthly:
                interval = int((len(l) / float(keep_monthly)) + 0.5)
                for j in range(0, len(l)):
                    if not j % interval == 0:
                        l[j].keep = False
            i += len(l)
        if delete:
            for snap in snaps:
                if not snap.keep:
                    snap.delete()
        return snaps
                
    def grow(self, size):
        pass

    def copy(self, snapshot):
        pass

    def get_snapshot_from_date(self, date):
        pass

    def delete(self, delete_ebs_volume=False):
        if delete_ebs_volume:
            self.detach()
            ec2 = self.get_ec2_connection()
            ec2.delete_volume(self.volume_id)
        Model.delete(self)

    def archive(self):
        # snapshot volume, trim snaps, delete volume-id
        pass
    

