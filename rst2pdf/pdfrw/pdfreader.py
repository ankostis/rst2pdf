# A part of pdfrw (pdfrw.googlecode.com)
# Copyright (C) 2006-2009 Patrick Maupin, Austin, Texas
# MIT license -- See LICENSE.txt for details

'''
The PdfReader class reads an entire PDF file into memory and
parses the top-level container objects.  (It does not parse
into streams.)  The object subclasses PdfDict, and the
document pages are stored in a list in the pages attribute
of the object.
'''
import gc

from .errors import PdfParseError, log
from .tokens import PdfTokens
from .objects import PdfDict, PdfArray, PdfName, PdfObject, PdfIndirect
from .uncompress import uncompress

class PdfReader(PdfDict):

    warned_bad_stream_start = False  # Use to keep from spewing warnings
    warned_bad_stream_end = False  # Use to keep from spewing warnings

    def findindirect(self, objnum, gennum):
        ''' Return a previously loaded indirect object, or create
            a placeholder for it.
        '''
        key = int(objnum), int(gennum)
        result = self.indirect_objects.get(key)
        if result is None:
            self.indirect_objects[key] = result = PdfIndirect(key)
            self.deferred_objects.add(key)
            result._loader = self.loadindirect
        return result

    def readarray(self, source, PdfArray=PdfArray):
        ''' Found a [ token.  Parse the tokens after that.
        '''
        specialget = self.special.get
        result = []
        pop = result.pop
        append = result.append

        for value in source:
            if value in b']R':
                if value == b']':
                    break
                generation = pop()
                value = self.findindirect(pop(), generation)
            else:
                func = specialget(value)
                if func is not None:
                    value = func(source)
            append(value)
        return PdfArray(result)

    def readdict(self, source, PdfDict=PdfDict):
        ''' Found a << token.  Parse the tokens after that.
        '''
        specialget = self.special.get
        result = PdfDict()

        tok = next(source)
        while tok != b'>>':
            if not tok.startswith(b'/'):
                source.exception('Expected PDF /name object')
            key = tok
            value = next(source)
            func = specialget(value)
            if func is not None:
                value = func(source)
                tok = next(source)
            else:
                tok = next(source)
                if value.isdigit() and tok.isdigit():
                    if next(source) != b'R':
                        source.exception('Expected "R" following two integers')
                    value = self.findindirect(value, tok)
                    tok = next(source)
            result[key] = value
        return result

    def empty_obj(self, source, PdfObject=PdfObject):
        ''' Some silly git put an empty object in the
            file.  Back up so the caller sees the endobj.
        '''
        source.floc = source.tokstart

    def badtoken(self, source):
        ''' Didn't see that coming.
        '''
        source.exception('Unexpected delimiter')

    def findstream(self, obj, tok, source):
        ''' Figure out if there is a content stream
            following an object, and return the start
            pointer to the content stream if so.

            (We can't read it yet, because we might not
            know how long it is, because Length might
            be an indirect object.)
        '''

        isdict = isinstance(obj, PdfDict)
        if not isdict or tok != 'stream':
            source.exception("Expected 'endobj'%s token", isdict and " or 'stream'" or '')
        fdata = source.fdata
        startstream = source.tokstart + len(tok)
        gotcr = fdata[startstream] == b'\r'
        startstream += gotcr
        gotlf = fdata[startstream] == b'\n'
        startstream += gotlf
        if not gotlf:
            if not gotcr:
                source.exception(r'stream keyword not followed by \n')
            if not self.warned_bad_stream_start:
                source.warning(r"stream keyword terminated by \r without \n")
                self.private.warned_bad_stream_start = True
        return startstream

    def readstream(self, obj, startstream, source,
                     streamending='endstream endobj'.split()):
        fdata = source.fdata
        length = int(obj.Length)
        source.floc = target_endstream = startstream + length
        endit = source.multiple(2)
        obj._stream = fdata[startstream:target_endstream]
        if endit == streamending:
            return

        # The length attribute does not match the distance between the
        # stream and endstream keywords.

        do_warn, self.warned_bad_stream_end = self.warned_bad_stream_end, False

        # TODO:  Extract maxstream from dictionary of object offsets
        # and use rfind instead of find.
        maxstream = len(fdata) - 20
        endstream = fdata.find('endstream', startstream, maxstream)
        source.floc = startstream
        room = endstream - startstream
        if endstream < 0:
            source.error('Could not find endstream')
            return
        if length == room + 1 and fdata[startstream - 2:startstream] == '\r\n':
            source.warning(r"stream keyword terminated by \r without \n")
            obj._stream = fdata[startstream - 1:target_endstream - 1]
            return
        source.floc = endstream
        if length > room:
            source.error('stream /Length attribute (%d) appears to be too big (size %d) -- adjusting',
                             length, room)
            obj.stream = fdata[startstream:endstream]
            return
        if fdata[target_endstream:endstream].rstrip():
            source.error('stream /Length attribute (%d) might be smaller than data size (%d)',
                             length, room)
            return
        endobj = fdata.find('endobj', endstream, maxstream)
        if endobj < 0:
            source.error('Could not find endobj after endstream')
            return
        if fdata[endstream:endobj].rstrip() != 'endstream':
            source.error('Unexpected data between endstream and endobj')
            return
        source.error('Illegal endstream/endobj combination')

    def loadindirect(self, key):
        result = self.indirect_objects.get(key)
        if not isinstance(result, PdfIndirect):
            return result
        source = self.source
        offset = int(self.source.obj_offsets.get(key, '0'))
        if not offset:
            log.warning("Did not find PDF object %s" % (key,))
            return None

        # Read the object header and validate it
        objnum, gennum = key
        source.floc = offset
        objid = source.multiple(3)
        ok = len(objid) == 3
        ok = ok and objid[0].isdigit() and int(objid[0]) == objnum
        ok = ok and objid[1].isdigit() and int(objid[1]) == gennum
        ok = ok and objid[2] == b'obj'
        if not ok:
            source.floc = offset
            next(source)
            objheader = b'%d %d obj' % (objnum, gennum)
            fdata = source.fdata
            offset2 = fdata.find(b'\n' + objheader) + 1 or fdata.find(b'\r' + objheader) + 1
            if not offset2 or fdata.find(fdata[offset2 - 1] + objheader, offset2) > 0:
                source.warning("Expected indirect object '%s'" % objheader)
                return None
            source.warning("Indirect object %s found at incorrect offset %d (expected offset %d)" %
                                     (objheader, offset2, offset))
            source.floc = offset2 + len(objheader)

        # Read the object, and call special code if it starts
        # an array or dictionary
        obj = next(source)
        func = self.special.get(obj)
        if func is not None:
            obj = func(source)

        self.indirect_objects[key] = obj
        self.deferred_objects.remove(key)

        # Mark the object as indirect, and
        # add it to the list of streams if it starts a stream
        obj.indirect = key
        tok = next(source)
        if tok != b'endobj':
            self.readstream(obj, self.findstream(obj, tok, source), source)
        return obj

    def findxref(fdata):
        ''' Find the cross reference section at the end of a file
        '''
        startloc = fdata.rfind(b'startxref')
        if startloc < 0:
            raise PdfParseError('Did not find "startxref" at end of file')
        source = PdfTokens(fdata, startloc, False)
        tableloc = source.next_default()
        if not tableloc.isdigit():
            source.exception('Expected table location')
        if source.next_default().rstrip().lstrip(b'%') != b'EOF':
            source.exception('Expected %%EOF')
        return startloc, PdfTokens(fdata, int(tableloc), True)
    findxref = staticmethod(findxref)

    def parsexref(self, source):
        ''' Parse (one of) the cross-reference file section(s)
        '''
        setdefault = source.obj_offsets.setdefault
        add_offset = source.all_offsets.append
        tok = next(source)
        if tok != 'xref':
            source.exception('Expected "xref" keyword')
        start = source.floc
        try:
            while 1:
                tok = next(source)
                if tok == 'trailer':
                    return
                startobj = int(tok)
                for objnum in range(startobj, startobj + int(next(source))):
                    offset = int(next(source))
                    generation = int(next(source))
                    inuse = next(source)
                    if inuse == 'n':
                        if offset != 0:
                            setdefault((objnum, generation), offset)
                            add_offset(offset)
                    elif inuse != 'f':
                        raise ValueError
        except:
            pass
        try:
        # Table formatted incorrectly.  See if we can figure it out anyway.
            end = source.fdata.rindex('trailer', start)
            table = source.fdata[start:end].splitlines()
            for line in table:
                tokens = line.split()
                if len(tokens) == 2:
                    objnum = int(tokens[0])
                elif len(tokens) == 3:
                    offset, generation, inuse = int(tokens[0]), int(tokens[1]), tokens[2]
                    if offset != 0 and inuse == 'n':
                        setdefault((objnum, generation), offset)
                        add_offset(offset)
                    objnum += 1
                elif tokens:
                    log.error('Invalid line in xref table: %s' % repr(line))
                    raise ValueError
            log.warning('Badly formatted xref table')
            source.floc = end
            next(source)
        except:
            source.floc = start
            source.exception('Invalid table format')

    def readpages(self, node):
        pagename = PdfName.Page
        pagesname = PdfName.Pages
        catalogname = PdfName.Catalog
        typename = PdfName.Type
        kidname = PdfName.Kids

        # PDFs can have arbitrarily nested Pages/Page
        # dictionary structures.
        def readnode(node):
            nodetype = node[typename]
            if nodetype == pagename:
                yield node
            elif nodetype == pagesname:
                for node in node[kidname]:
                    for node in readnode(node):
                        yield node
            elif nodetype == catalogname:
                for node in readnode(node[pagesname]):
                    yield node
            else:
                log.error('Expected /Page or /Pages dictionary, got %s' % repr(node))
        try:
            return list(readnode(node))
        except (AttributeError, TypeError) as s:
            log.error('Invalid page tree: %s' % s)
            return []

    def __init__(self, fname=None, fdata=None, decompress=False, disable_gc=True):

        # Runs a lot faster with GC off.
        disable_gc = disable_gc and gc.isenabled()
        try:
            if disable_gc:
                gc.disable()
            if fname is not None:
                assert fdata is None
                # Allow reading preexisting streams like pyPdf
                if hasattr(fname, 'read'):
                    fdata = fname.read()
                else:
                    try:
                        f = open(fname, 'rb')
                        fdata = f.read()
                        f.close()
                    except IOError:
                        raise PdfParseError('Could not read PDF file %s' % fname)

            assert fdata is not None
            if not fdata.startswith(b'%PDF-'):
                startloc = fdata.find(b'%PDF-')
                if startloc >= 0:
                    log.warning('PDF header not at beginning of file')
                else:
                    lines = fdata.lstrip().splitlines()
                    if not lines:
                        raise PdfParseError('Empty PDF file!')
                    raise PdfParseError('Invalid PDF header: %s' % repr(lines[0]))

            endloc = fdata.rfind(b'%EOF')
            if endloc < 0:
                raise PdfParseError('EOF mark not found: %s' % repr(fdata[-20:]))
            endloc += 6
            junk = fdata[endloc:]
            fdata = fdata[:endloc]
            if junk.rstrip(b'\00').strip():
                log.warning('Extra data at end of file')

            private = self.private
            private.indirect_objects = {}
            private.deferred_objects = set()
            private.special = {b'<<': self.readdict,
                               b'[': self.readarray,
                               b'endobj': self.empty_obj,
                               }
            for tok in b'\ ( ) < > { } ] >> %'.split():
                self.special[tok] = self.badtoken


            startloc, source = self.findxref(fdata)
            private.source = source
            xref_table_list = []
            source.all_offsets = []
            while 1:
                source.obj_offsets = {}
                # Loop through all the cross-reference tables
                self.parsexref(source)
                tok = next(source)
                if tok != b'<<':
                    source.exception('Expected "<<" starting catalog')

                newdict = self.readdict(source)

                token = next(source)
                if token != b'startxref' and not xref_table_list:
                    source.warning('Expected "startxref" at end of xref table')

                # Loop if any previously-written tables.
                prev = newdict.Prev
                if prev is None:
                    break
                if not xref_table_list:
                    newdict.Prev = None
                    original_indirect = self.indirect_objects.copy()
                    original_newdict = newdict
                source.floc = int(prev)
                xref_table_list.append(source.obj_offsets)
                self.indirect_objects.clear()

            if xref_table_list:
                for update in reversed(xref_table_list):
                    source.obj_offsets.update(update)
                self.indirect_objects.clear()
                self.indirect_objects.update(original_indirect)
                newdict = original_newdict
            self.update(newdict)

            # self.read_all_indirect(source)
            private.pages = self.readpages(self.Root)
            if decompress:
                self.uncompress()

            # For compatibility with pyPdf
            private.numPages = len(self.pages)
        finally:
            if disable_gc:
                gc.enable()

    # For compatibility with pyPdf
    def getPage(self, pagenum):
        return self.pages[pagenum]

    def read_all(self):
        deferred = self.deferred_objects
        prev = set()
        while 1:
            new = deferred - prev
            if not new:
                break
            prev |= deferred
            for key in new:
                self.loadindirect(key)

    def uncompress(self):
        self.read_all()
        uncompress(iter(self.indirect_objects.values()))
