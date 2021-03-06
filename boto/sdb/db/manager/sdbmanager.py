# Copyright (c) 2006,2007,2008 Mitch Garnaat http://garnaat.org/
# Copyright (c) 2010 Chris Moyer http://coredumped.org/
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
import boto
import re
from boto.utils import find_class
import uuid
from boto.sdb.db.key import Key
from boto.sdb.db.model import Model
from boto.sdb.db.blob import Blob
from boto.sdb.db.property import ListProperty, MapProperty
from datetime import datetime
from boto.exception import SDBPersistenceError

ISO8601 = '%Y-%m-%dT%H:%M:%SZ'


class SDBConverter:
    """
    Responsible for converting base Python types to format compatible with underlying
    database.  For SimpleDB, that means everything needs to be converted to a string
    when stored in SimpleDB and from a string when retrieved.

    To convert a value, pass it to the encode or decode method.  The encode method
    will take a Python native value and convert to DB format.  The decode method will
    take a DB format value and convert it to Python native format.  To find the appropriate
    method to call, the generic encode/decode methods will look for the type-specific
    method by searching for a method called "encode_<type name>" or "decode_<type name>".
    """
    def __init__(self, manager):
        self.manager = manager
        self.type_map = { bool : (self.encode_bool, self.decode_bool),
                          int : (self.encode_int, self.decode_int),
                          long : (self.encode_long, self.decode_long),
                          float : (self.encode_float, self.decode_float),
                          Model : (self.encode_reference, self.decode_reference),
                          Key : (self.encode_reference, self.decode_reference),
                          datetime : (self.encode_datetime, self.decode_datetime),
                          Blob: (self.encode_blob, self.decode_blob),
                      }

    def encode(self, item_type, value):
        try:
            if Model in item_type.mro():
                item_type = Model
        except:
            pass
        if item_type in self.type_map:
            encode = self.type_map[item_type][0]
            return encode(value)
        return value

    def decode(self, item_type, value):
        if item_type in self.type_map:
            decode = self.type_map[item_type][1]
            return decode(value)
        return value

    def encode_list(self, prop, value):
        if value in (None, []):
            return []
        if not isinstance(value, list):
            # This is a little trick to avoid encoding when it's just a single value,
            # since that most likely means it's from a query
            item_type = getattr(prop, "item_type")
            return self.encode(item_type, value)
        # Just enumerate(value) won't work here because
        # we need to add in some zero padding
        # We support lists up to 1,000 attributes, since
        # SDB technically only supports 1024 attributes anyway
        values = {}
        for k,v in enumerate(value):
            values["%03d" % k] = v
        return self.encode_map(prop, values)

    def encode_map(self, prop, value):
        if value == None:
            return None
        if not isinstance(value, dict):
            raise ValueError, 'Expected a dict value, got %s' % type(value)
        new_value = []
        for key in value:
            item_type = getattr(prop, "item_type")
            if Model in item_type.mro():
                item_type = Model
            encoded_value = self.encode(item_type, value[key])
            if encoded_value != None and encoded_value != "None":
                new_value.append('%s:%s' % (key, encoded_value))
        return new_value

    def encode_prop(self, prop, value):
        if isinstance(prop, ListProperty):
            return self.encode_list(prop, value)
        elif isinstance(prop, MapProperty):
            return self.encode_map(prop, value)
        else:
            return self.encode(prop.data_type, value)

    def decode_list(self, prop, value):
        if not isinstance(value, list):
            value = [value]
        if hasattr(prop, 'item_type'):
            item_type = getattr(prop, "item_type")
            dec_val = {}
            for val in value:
                if val != "None" and val != None:
                    k,v = self.decode_map_element(item_type, val)
                    try:
                        k = int(k)
                    except:
                        k = v
                    dec_val[k] = v
            value = dec_val.values()
        return value

    def decode_map(self, prop, value):
        if not isinstance(value, list):
            value = [value]
        ret_value = {}
        item_type = getattr(prop, "item_type")
        for val in value:
            k,v = self.decode_map_element(item_type, val)
            ret_value[k] = v
        return ret_value

    def decode_map_element(self, item_type, value):
        """Decode a single element for a map"""
        key = value
        if ":" in value:
            key, value = value.split(':',1)
        if Model in item_type.mro():
            value = item_type(id=value)
        else:
            value = self.decode(item_type, value)
        return (key, value)

    def decode_prop(self, prop, value):
        if isinstance(prop, ListProperty):
            return self.decode_list(prop, value)
        elif isinstance(prop, MapProperty):
            return self.decode_map(prop, value)
        else:
            return self.decode(prop.data_type, value)

    def encode_int(self, value):
        value = int(value)
        value += 2147483648
        return '%010d' % value

    def decode_int(self, value):
        try:
            value = int(value)
        except:
            boto.log.error("Error, %s is not an integer" % value)
            value = 0
        value = int(value)
        value -= 2147483648
        return int(value)

    def encode_long(self, value):
        value = long(value)
        value += 9223372036854775808
        return '%020d' % value

    def decode_long(self, value):
        value = long(value)
        value -= 9223372036854775808
        return value

    def encode_bool(self, value):
        if value == True or str(value).lower() in ("true", "yes"):
            return 'true'
        else:
            return 'false'

    def decode_bool(self, value):
        if value.lower() == 'true':
            return True
        else:
            return False

    def encode_float(self, value):
        """
        See http://tools.ietf.org/html/draft-wood-ldapext-float-00.
        """
        s = '%e' % value
        l = s.split('e')
        mantissa = l[0].ljust(18, '0')
        exponent = l[1]
        if value == 0.0:
            case = '3'
            exponent = '000'
        elif mantissa[0] != '-' and exponent[0] == '+':
            case = '5'
            exponent = exponent[1:].rjust(3, '0')
        elif mantissa[0] != '-' and exponent[0] == '-':
            case = '4'
            exponent = 999 + int(exponent)
            exponent = '%03d' % exponent
        elif mantissa[0] == '-' and exponent[0] == '-':
            case = '2'
            mantissa = '%f' % (10 + float(mantissa))
            mantissa = mantissa.ljust(18, '0')
            exponent = exponent[1:].rjust(3, '0')
        else:
            case = '1'
            mantissa = '%f' % (10 + float(mantissa))
            mantissa = mantissa.ljust(18, '0')
            exponent = 999 - int(exponent)
            exponent = '%03d' % exponent
        return '%s %s %s' % (case, exponent, mantissa)

    def decode_float(self, value):
        case = value[0]
        exponent = value[2:5]
        mantissa = value[6:]
        if case == '3':
            return 0.0
        elif case == '5':
            pass
        elif case == '4':
            exponent = '%03d' % (int(exponent) - 999)
        elif case == '2':
            mantissa = '%f' % (float(mantissa) - 10)
            exponent = '-' + exponent
        else:
            mantissa = '%f' % (float(mantissa) - 10)
            exponent = '%03d' % abs((int(exponent) - 999))
        return float(mantissa + 'e' + exponent)

    def encode_datetime(self, value):
        if isinstance(value, str) or isinstance(value, unicode):
            return value
        return value.strftime(ISO8601)

    def decode_datetime(self, value):
        try:
            return datetime.strptime(value, ISO8601)
        except:
            return None

    def encode_reference(self, value):
        if value in (None, 'None', '', ' '):
            return 'None'
        if isinstance(value, str) or isinstance(value, unicode):
            return value
        else:
            return value.id

    def decode_reference(self, value):
        if not value or value == "None":
            return None
        return value

    def encode_blob(self, value):
        if not value:
            return None

        if not value.id:
            bucket = self.manager.get_blob_bucket()
            key = bucket.new_key(str(uuid.uuid4()))
            value.id = "s3://%s/%s" % (key.bucket.name, key.name)
        else:
            match = re.match("^s3:\/\/([^\/]*)\/(.*)$", value.id)
            if match:
                s3 = self.manager.get_s3_connection()
                bucket = s3.get_bucket(match.group(1), validate=False)
                key = bucket.get_key(match.group(2))
            else:
                raise SDBPersistenceError("Invalid Blob ID: %s" % value.id)

        if value.value != None:
            key.set_contents_from_string(value.value)
        return value.id


    def decode_blob(self, value):
        if not value:
            return None
        match = re.match("^s3:\/\/([^\/]*)\/(.*)$", value)
        if match:
            s3 = self.manager.get_s3_connection()
            bucket = s3.get_bucket(match.group(1), validate=False)
            key = bucket.get_key(match.group(2))
        else:
            return None
        if key:
            return Blob(file=key, id="s3://%s/%s" % (key.bucket.name, key.name))
        else:
            return None

class SDBManager(object):
    
    def __init__(self, cls, db_name, db_user, db_passwd,
                 db_host, db_port, db_table, ddl_dir, enable_ssl, consistent=None):
        self.cls = cls
        self.db_name = db_name
        self.db_user = db_user
        self.db_passwd = db_passwd
        self.db_host = db_host
        self.db_port = db_port
        self.db_table = db_table
        self.ddl_dir = ddl_dir
        self.enable_ssl = enable_ssl
        self.s3 = None
        self.bucket = None
        self.converter = SDBConverter(self)
        self._sdb = None
        self._domain = None
        if consistent == None and hasattr(cls, "__consistent"):
            consistent = cls.__consistent__
        self.consistent = consistent

    @property
    def sdb(self):
        if self._sdb is None:
            self._connect()
        return self._sdb

    @property
    def domain(self):
        if self._domain is None:
            self._connect()
        return self._domain

    def _connect(self):
        self._sdb = boto.connect_sdb(aws_access_key_id=self.db_user,
                                    aws_secret_access_key=self.db_passwd,
                                    is_secure=self.enable_ssl)
        # This assumes that the domain has already been created
        # It's much more efficient to do it this way rather than
        # having this make a roundtrip each time to validate.
        # The downside is that if the domain doesn't exist, it breaks
        self._domain = self._sdb.lookup(self.db_name, validate=False)
        if not self._domain:
            self._domain = self._sdb.create_domain(self.db_name)

    def _object_lister(self, cls, query_lister):
        for item in query_lister:
            obj = self.get_object(cls, item.name, item)
            if obj:
                yield obj
            
    def encode_value(self, prop, value):
        if value == None:
            return None
        if not prop:
            return str(value)
        return self.converter.encode_prop(prop, value)

    def decode_value(self, prop, value):
        return self.converter.decode_prop(prop, value)

    def get_s3_connection(self):
        if not self.s3:
            self.s3 = boto.connect_s3(self.db_user, self.db_passwd)
        return self.s3

    def get_blob_bucket(self, bucket_name=None):
        s3 = self.get_s3_connection()
        bucket_name = "%s-%s" % (s3.aws_access_key_id, self.domain.name)
        bucket_name = bucket_name.lower()
        try:
            self.bucket = s3.get_bucket(bucket_name)
        except:
            self.bucket = s3.create_bucket(bucket_name)
        return self.bucket
            
    def load_object(self, obj):
        if not obj._loaded:
            a = self.domain.get_attributes(obj.id,consistent_read=self.consistent)
            if a.has_key('__type__'):
                for prop in obj.properties(hidden=False):
                    if a.has_key(prop.name):
                        value = self.decode_value(prop, a[prop.name])
                        value = prop.make_value_from_datastore(value)
                        try:
                            setattr(obj, prop.name, value)
                        except Exception, e:
                            boto.log.exception(e)
            obj._loaded = True
        
    def get_object(self, cls, id, a=None):
        obj = None
        if not a:
            a = self.domain.get_attributes(id,consistent_read=self.consistent)
        if a.has_key('__type__'):
            if not cls or a['__type__'] != cls.__name__:
                cls = find_class(a['__module__'], a['__type__'])
            if cls:
                params = {}
                for prop in cls.properties(hidden=False):
                    if a.has_key(prop.name):
                        value = self.decode_value(prop, a[prop.name])
                        value = prop.make_value_from_datastore(value)
                        params[prop.name] = value
                obj = cls(id, **params)
                obj._loaded = True
            else:
                s = '(%s) class %s.%s not found' % (id, a['__module__'], a['__type__'])
                boto.log.info('sdbmanager: %s' % s)
        return obj
        
    def get_object_from_id(self, id):
        return self.get_object(None, id)

    def query(self, query):
        query_str = "select * from `%s` %s" % (self.domain.name, self._build_filter_part(query.model_class, query.filters, query.sort_by))
        if query.limit:
            query_str += " limit %s" % query.limit
        rs = self.domain.select(query_str, max_items=query.limit, next_token = query.next_token)
        query.rs = rs
        return self._object_lister(query.model_class, rs)

    def count(self, cls, filters):
        """
        Get the number of results that would
        be returned in this query
        """
        query = "select count(*) from `%s` %s" % (self.domain.name, self._build_filter_part(cls, filters))
        count =  int(self.domain.select(query).next()["Count"])
        return count


    def _build_filter(self, property, name, op, val):
        if val == None:
            if op in ('is','='):
                return "`%s` is null" % name
            elif op in ('is not', '!='):
                return "`%s` is not null" % name
            else:
                val = ""
        if property.__class__ == ListProperty:
            if op in ("is", "="):
                op = "like"
            elif op in ("!=", "not"):
                op = "not like"
            if not(op == "like" and val.startswith("%")):
                val = "%%:%s" % val
        return "`%s` %s '%s'" % (name, op, val.replace("'", "''"))

    def _build_filter_part(self, cls, filters, order_by=None):
        """
        Build the filter part
        """
        import types
        query_parts = []
        order_by_filtered = False
        if order_by:
            if order_by[0] == "-":
                order_by_method = "desc";
                order_by = order_by[1:]
            else:
                order_by_method = "asc";

        for filter in filters:
            filter_parts = []
            filter_props = filter[0]
            if type(filter_props) != list:
                filter_props = [filter_props]
            for filter_prop in filter_props:
                (name, op) = filter_prop.strip().split(" ", 1)
                value = filter[1]
                property = cls.find_property(name)
                if name == order_by:
                    order_by_filtered = True
                if types.TypeType(value) == types.ListType:
                    filter_parts_sub = []
                    for val in value:
                        val = self.encode_value(property, val)
                        if isinstance(val, list):
                            for v in val:
                                filter_parts_sub.append(self._build_filter(property, name, op, v))
                        else:
                            filter_parts_sub.append(self._build_filter(property, name, op, val))
                    filter_parts.append("(%s)" % (" or ".join(filter_parts_sub)))
                else:
                    val = self.encode_value(property, value)
                    if isinstance(val, list):
                        for v in val:
                            filter_parts.append(self._build_filter(property, name, op, v))
                    else:
                        filter_parts.append(self._build_filter(property, name, op, val))
            query_parts.append("(%s)" % (" or ".join(filter_parts)))


        type_query = "(`__type__` = '%s'" % cls.__name__
        for subclass in self._get_all_decendents(cls).keys():
            type_query += " or `__type__` = '%s'" % subclass
        type_query +=")"
        query_parts.append(type_query)

        order_by_query = ""
        if order_by:
            if not order_by_filtered:
                query_parts.append("`%s` like '%%'" % order_by)
            order_by_query = " order by `%s` %s" % (order_by, order_by_method)

        if len(query_parts) > 0:
            return "where %s %s" % (" and ".join(query_parts), order_by_query)
        else:
            return ""


    def _get_all_decendents(self, cls):
        """Get all decendents for a given class"""
        decendents = {}
        for sc in cls.__sub_classes__:
            decendents[sc.__name__] = sc
            decendents.update(self._get_all_decendents(sc))
        return decendents

    def query_gql(self, query_string, *args, **kwds):
        raise NotImplementedError, "GQL queries not supported in SimpleDB"

    def save_object(self, obj):
        if not obj.id:
            obj.id = str(uuid.uuid4())

        attrs = {'__type__' : obj.__class__.__name__,
                 '__module__' : obj.__class__.__module__,
                 '__lineage__' : obj.get_lineage()}
        for property in obj.properties(hidden=False):
            value = property.get_value_for_datastore(obj)
            if value is not None:
                value = self.encode_value(property, value)
            if value == []:
                value = None
            attrs[property.name] = value
            if property.unique:
                try:
                    args = {property.name: value}
                    obj2 = obj.find(**args).next()
                    if obj2.id != obj.id:
                        raise SDBPersistenceError("Error: %s must be unique!" % property.name)
                except(StopIteration):
                    pass
        self.domain.put_attributes(obj.id, attrs, replace=True)

    def delete_object(self, obj):
        self.domain.delete_attributes(obj.id)

    def set_property(self, prop, obj, name, value):
        value = prop.get_value_for_datastore(obj)
        value = self.encode_value(prop, value)
        if prop.unique:
            try:
                args = {prop.name: value}
                obj2 = obj.find(**args).next()
                if obj2.id != obj.id:
                    raise SDBPersistenceError("Error: %s must be unique!" % prop.name)
            except(StopIteration):
                pass
        self.domain.put_attributes(obj.id, {name : value}, replace=True)

    def get_property(self, prop, obj, name):
        a = self.domain.get_attributes(obj.id,consistent_read=self.consistent)

        # try to get the attribute value from SDB
        if name in a:
            value = self.decode_value(prop, a[name])
            value = prop.make_value_from_datastore(value)
            setattr(obj, prop.name, value)
            return value
        raise AttributeError, '%s not found' % name

    def set_key_value(self, obj, name, value):
        self.domain.put_attributes(obj.id, {name : value}, replace=True)

    def delete_key_value(self, obj, name):
        self.domain.delete_attributes(obj.id, name)

    def get_key_value(self, obj, name):
        a = self.domain.get_attributes(obj.id, name,consistent_read=self.consistent)
        if a.has_key(name):
            return a[name]
        else:
            return None
    
    def get_raw_item(self, obj):
        return self.domain.get_item(obj.id)
        
