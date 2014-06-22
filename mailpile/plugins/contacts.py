from gettext import gettext as _

from mailpile.plugins import PluginManager
from mailpile.commands import Command, Action
from mailpile.mailutils import Email, ExtractEmails, ExtractEmailAndName
from mailpile.vcard import MailpileVCard, VCardLine, AddressInfo
from mailpile.util import *


_plugins = PluginManager(builtin=__file__)


##[ VCards ]########################################

class VCardCommand(Command):
    VCARD = "vcard"
    IS_USER_ACTIVITY = True

    class CommandResult(Command.CommandResult):
        IGNORE = ('line_id', 'pid', 'x-rank')

        def as_text(self):
            try:
                return self._as_text()
            except (KeyError, ValueError, IndexError, TypeError):
                return ''

        def _as_text(self):
            if isinstance(self.result, dict):
                co = self.command_obj
                if co.VCARD in self.result:
                    return self._vcards_as_text([self.result[co.VCARD]])
                if co.VCARD + 's' in self.result:
                    return self._vcards_as_text(self.result[co.VCARD + 's'])
            return Command.CommandResult.as_text(self)

        def _vcards_as_text(self, result):
            lines = []
            b64re = re.compile('base64,.*$')
            for card in result:
                if isinstance(card, list):
                    for line in card:
                        key = line.name
                        data = re.sub(b64re, _('(BASE64 ENCODED DATA)'),
                                      unicode(line[key]))
                        attrs = ', '.join([('%s=%s' % (k, v))
                                           for k, v in line.attrs
                                           if k not in ('pid',)])
                        if attrs:
                            attrs = ' (%s)' % attrs
                        lines.append('%3.3s %-5.5s %s: %s%s'
                                     % (line.line_id,
                                        line.get('pid', ''),
                                        key, data, attrs))
                    lines.append('')
                else:
                    emails = [k['email'] for k in card['email']]
                    photos = [k['photo'] for k in card.get('photo', [])]
                    lines.append('%s %-26.26s %s'
                                 % (photos and ':)' or '  ',
                                    card['fn'],
                                    ', '.join(emails)))
                    for key in [k['key'].split(',')[-1]
                                for k in card.get('key', [])]:
                        lines.append('   %-26.26s key:%s' % ('', key))
            return '\n'.join(lines)

    def _make_new_vcard(self, handle, name):
        l = [VCardLine(name='fn', value=name),
             VCardLine(name='kind', value=self.KIND)]
        if self.KIND in ('individual', 'profile'):
            return MailpileVCard(VCardLine(name='email',
                                           value=handle, type='pref'), *l)
        else:
            return MailpileVCard(VCardLine(name='nickname', value=handle), *l)

    def _valid_vcard_handle(self, vc_handle):
        return (vc_handle and '@' in vc_handle[1:])

    def _pre_delete_vcard(self, vcard):
        pass

    def _vcard_list(self, vcards, mode='mpCard', info=None):
        info = info or {}
        if mode == 'lines':
            data = [x.as_lines() for x in vcards if x]
        else:
            data = [x.as_mpCard() for x in vcards if x]
        info.update({
            self.VCARD + 's': data,
            "count": len(vcards)
        })
        return info


class VCard(VCardCommand):
    """Display a single vcard"""
    SYNOPSIS = (None, 'vcards/view', None, '<nickname>')
    ORDER = ('Internals', 6)
    KIND = ''

    def command(self, save=True):
        session, config = self.session, self.session.config
        vcards = []
        for email in self.args:
            vcard = config.vcards.get_vcard(email)
            if vcard:
                vcards.append(vcard)
            else:
                session.ui.warning('No such %s: %s' % (self.VCARD, email))
        if len(vcards) == 1:
            result = {self.VCARD: vcards[0].as_mpCard()}
        else:
            result = {self.VCARD + 's': [x.as_mpCard() for x in vcards]}
        return self._success(_('Found %d results') % len(vcards),
                             result=result)


class AddVCard(VCardCommand):
    """Add one or more vcards"""
    SYNOPSIS = (None, 'vcards/add', None, '[all] <msgs> OR <email> = <name>')
    ORDER = ('Internals', 6)
    KIND = ''
    HTTP_CALLABLE = ('POST', 'PUT', 'GET')
    HTTP_POST_VARS = {
        'email': 'e-mail address',
        'name': 'Contact name',
        'mid': 'Message ID'
    }

    def _add_from_messages(self, args, add_recipients):
        pairs, idx = [], self._idx()
        for email in [Email(idx, i) for i in self._choose_messages(args)]:
            msg_info = email.get_msg_info()
            pairs.append(ExtractEmailAndName(msg_info[idx.MSG_FROM]))
            if add_recipients:
                people = (idx.expand_to_list(msg_info) +
                          idx.expand_to_list(msg_info, field=idx.MSG_CC))
                for e in people:
                    pairs.append(ExtractEmailAndName(e))
        return pairs

    def command(self, recipients=False, quietly=False):
        session, config, idx = self.session, self.session.config, self._idx()
        args = list(self.args)

        if self.data.get('_method', 'not-http').upper() == 'GET':
            return self._success(_('Add contacts here!'), {
                'form': self.HTTP_POST_VARS
            })

        if (len(args) > 2
                and args[1] == '='
                and self._valid_vcard_handle(args[0])):
            pairs = [(args[0], ' '.join(args[2:]))]

        elif self.data:
            if self.data.get('name') and self.data.get('email'):
                pairs = zip(self.data["email"], self.data["name"])
            elif self.data.get('mid'):
                mids = self.data.get('mid')
                pairs = self._add_from_messages(
                    ['=%s' % mid.replace('=', '') for mid in mids])
        else:
            if args and args[0] == 'all':
                recipients = args.pop(0) and True
            pairs = self._add_from_messages(args, recipients)

        if pairs:
            vcards = []
            for handle, name in pairs:
                if handle.lower() in config.vcards:
                    if not quietly:
                        session.ui.warning('Already exists: %s' % handle)
                    if self.KIND != 'profile':
                        continue
                vcard = self._make_new_vcard(handle.lower(), name)
                config.vcards.add_vcards(vcard)
                vcards.append(vcard)
        else:
            return self._error('Nothing to do!')
        return self._success(_('Added %d contacts') % len(vcards),
            result={self.VCARD + 's': [x.as_mpCard() for x in vcards]})


class RemoveVCard(VCardCommand):
    """Delete vcards"""
    SYNOPSIS = (None, 'vcards/remove', None, '<email|x-mailpile-rid>')
    ORDER = ('Internals', 6)
    KIND = ''
    HTTP_CALLABLE = ('POST', 'DELETE')

    def command(self):
        session, config = self.session, self.session.config
        removed = []
        for handle in self.args:
            vcard = config.vcards.get_vcard(handle)
            if vcard:
                self._pre_delete_vcard(vcard)
                config.vcards.del_vcards(vcard)
                removed.append(handle)
            else:
                session.ui.error(_('No such contact: %s') % handle)
        if removed:
            return self._success(_('Removed contacts: %s')
                                 % ', '.join(removed))
        else:
            return self._error(_('No contacts found'))


class VCardAddLines(VCardCommand):
    """Add a lines to a VCard"""
    SYNOPSIS = (None, 'vcards/addlines', None, '<email> <[<LID>=]line> ...')
    ORDER = ('Internals', 6)
    KIND = ''
    HTTP_CALLABLE = ('POST', 'UPDATE')

    def command(self):
        session, config = self.session, self.session.config
        handle, lines = self.args[0], self.args[1:]
        vcard = config.vcards.get_vcard(handle)
        if not vcard:
            return self._error('%s not found: %s' % (self.VCARD, handle))
        config.vcards.deindex_vcard(vcard)
        try:
            for l in lines:
                if '=' in l[:5]:
                    ln, l = l.split('=', 1)
                    vcard.set_line(int(ln.strip()), VCardLine(l.strip()))
                else:
                    vcard.add(VCardLine(l))
            vcard.save()
            return self._success(_("Added %d lines") % len(lines),
                result=self._vcard_list([vcard], info={
                    'updated': handle,
                    'added': len(lines)
                }))
        except KeyboardInterrupt:
            raise
        except:
            config.vcards.index_vcard(vcard)
            self._ignore_exception()
            return self._error(_('Error adding lines to %s') % handle)
        finally:
            config.vcards.index_vcard(vcard)


class VCardRemoveLines(VCardCommand):
    """Remove lines from a VCard"""
    SYNOPSIS = (None, 'vcards/rmlines', None, '<email> <line IDs>')
    ORDER = ('Internals', 6)
    KIND = ''
    HTTP_CALLABLE = ('POST', 'UPDATE')

    def command(self):
        session, config = self.session, self.session.config
        handle, line_ids = self.args[0], self.args[1:]
        vcard = config.vcards.get_vcard(handle)
        if not vcard:
            return self._error('%s not found: %s' % (self.VCARD, handle))
        config.vcards.deindex_vcard(vcard)
        removed = 0
        try:
            removed = vcard.remove(*[int(li) for li in line_ids])
            vcard.save()
            return self._success(_("Removed %d lines") % removed,
                result=self._vcard_list([vcard], info={
                    'updated': handle,
                    'removed': removed
                }))
        except KeyboardInterrupt:
            raise
        except:
            config.vcards.index_vcard(vcard)
            self._ignore_exception()
            return self._error(_('Error removing lines from %s') % handle)
        finally:
            config.vcards.index_vcard(vcard)


class ListVCards(VCardCommand):
    """Find vcards"""
    SYNOPSIS = (None, 'vcards', None, '[--lines] [<terms>]')
    ORDER = ('Internals', 6)
    KIND = ''
    HTTP_QUERY_VARS = {
        'q': 'search terms',
        'format': 'lines or mpCard (default)',
        'count': 'how many to display (default=40)',
        'offset': 'skip how many in the display (default=0)',
    }
    HTTP_CALLABLE = ('GET')

    def command(self):
        session, config = self.session, self.session.config
        kinds = self.KIND and [self.KIND] or []
        args = list(self.args)

        if 'format' in self.data:
            fmt = self.data['format'][0]
        elif args and args[0] == '--lines':
            args.pop(0)
            fmt = 'lines'
        else:
            fmt = 'mpCard'

        if 'q' in self.data:
            terms = self.data['q']
        else:
            terms = args

        if 'count' in self.data:
            count = int(self.data['count'][0])
        else:
            count = 120

        if 'offset' in self.data:
            offset = int(self.data['offset'][0])
        else:
            offset = 0

        vcards = config.vcards.find_vcards(terms, kinds=kinds)
        total = len(vcards)
        vcards = vcards[offset:offset + count]
        return self._success(_("Listed %d/%d results") % (min(total, count),
                                                          total),
                             result=self._vcard_list(vcards, mode=fmt, info={
                   'terms': args,
                   'offset': offset,
                   'count': min(count, total),
                   'total': total,
                   'start': offset,
                   'end': offset + min(count, total - offset),
               }))


def ContactVCard(parent):
    """A factory for generating contact commands"""
    synopsis = [(t and t.replace('vcard', 'contact') or t)
                for t in parent.SYNOPSIS]
    synopsis[2] = synopsis[1]

    class ContactVCardCommand(parent):
        SYNOPSIS = tuple(synopsis)
        KIND = 'individual'
        ORDER = ('Tagging', 3)
        VCARD = "contact"

    return ContactVCardCommand


class Contact(ContactVCard(VCard)):
    """View contacts"""
    SYNOPSIS = (None, 'contacts/view', 'contacts/view', '[<email>]')

    def command(self, save=True):
        contact = VCard.command(self, save)
        # Tee-hee, monkeypatching results.
        contact["sent_messages"] = 0
        contact["received_messages"] = 0
        contact["last_contact_from"] = 10000000000000
        contact["last_contact_to"] = 10000000000000

        for email in contact["contact"]["email"]:
            s = Action(self.session, "search",
                       ["in:Sent", "to:%s" % (email["email"])]).as_dict()
            contact["sent_messages"] += s["result"]["stats"]["total"]
            for mid in s["result"]["thread_ids"]:
                msg = s["result"]["data"]["metadata"][mid]
                if msg["timestamp"] < contact["last_contact_to"]:
                    contact["last_contact_to"] = msg["timestamp"]
                    contact["last_contact_to_msg_url"] = msg["urls"]["thread"]

            s = Action(self.session, "search",
                       ["from:%s" % (email["email"])]).as_dict()
            contact["received_messages"] += s["result"]["stats"]["total"]
            for mid in s["result"]["thread_ids"]:
                msg = s["result"]["data"]["metadata"][mid]
                if msg["timestamp"] < contact["last_contact_from"]:
                    contact["last_contact_from"] = msg["timestamp"]
                    contact["last_contact_from_msg_url"
                            ] = msg["urls"]["thread"]

        if contact["last_contact_to"] == 10000000000000:
            contact["last_contact_to"] = False
            contact["last_contact_to_msg_url"] = ""

        if contact["last_contact_from"] == 10000000000000:
            contact["last_contact_from"] = False
            contact["last_contact_from_msg_url"] = ""

        return contact


class AddContact(ContactVCard(AddVCard)):
    """Add contacts"""


class RemoveContact(ContactVCard(RemoveVCard)):
    """Remove a contact"""


class ListContacts(ContactVCard(ListVCards)):
    SYNOPSIS = (None, 'contacts', 'contacts', '[--lines] [<terms>]')
    """Find contacts"""


class ContactImport(Command):
    """Import contacts"""
    SYNOPSIS = (None, 'contacts/import', 'contacts/import', '[<parameters>]')
    ORDER = ('Internals', 6)
    HTTP_CALLABLE = ('GET', )

    def command(self, format, terms=None, **kwargs):
        session, config = self.session, self.session.config

        if not format in PluginManager.CONTACT_IMPORTERS.keys():
            session.ui.error("No such import format")
            return False

        importer = PluginManager.CONTACT_IMPORTERS[format]

        if not all([x in kwargs.keys() for x in importer.required_parameters]):
            session.ui.error(
                _("Required paramter missing. Required parameters "
                  "are: %s") % ", ".join(importer.required_parameters))
            return False

        allparams = importer.required_parameters + importer.optional_parameters

        if not all([x in allparams for x in kwargs.keys()]):
            session.ui.error(
                _("Unknown parameter passed to importer. "
                  "Provided %s; but known parameters are: %s"
                  ) % (", ".join(kwargs), ", ".join(allparams)))
            return False

        imp = importer(kwargs)
        if terms:
            contacts = imp.filter_contacts(terms)
        else:
            contacts = imp.get_contacts()

        for importedcontact in contacts:
            # Check if contact exists. If yes, then update. Else create.
            pass


class ContactImporters(Command):
    """Return a list of contact importers"""
    SYNOPSIS = (None, 'contacts/importers', 'contacts/importers', '')
    ORDER = ('Internals', 6)
    HTTP_CALLABLE = ('GET', )

    def command(self):
        res = []
        for iname, importer in CONTACT_IMPORTERS.iteritems():
            r = {}
            r["short_name"] = iname
            r["format_name"] = importer.format_name
            r["format_description"] = importer.format_description
            r["optional_parameters"] = importer.optional_parameters
            r["required_parameters"] = importer.required_parameters
            res.append(r)

        return res


class AddressSearch(VCardCommand):
    """Find addresses (in contacts or mail index)"""
    SYNOPSIS = (None, 'search/address', 'search/address', '[<terms>]')
    ORDER = ('Searching', 6)
    HTTP_QUERY_VARS = {
        'q': 'search terms',
        'count': 'number of results',
        'offset': 'offset results'
    }

    def _boost_rank(self, term, *matches):
        boost = 0.0
        for match in matches:
            match = match.lower()
            if term in match:
                if match.startswith(term):
                    boost += 25 * (float(len(term)) / len(match))
                else:
                    boost += 5 * (float(len(term)) / len(match))
        return int(boost)

    def _vcard_addresses(self, cfg, terms):
        addresses = {}
        for vcard in cfg.vcards.find_vcards(terms, kinds=['individual',
                                                          'profile']):
            fn = vcard.get('fn')
            for email_vcl in vcard.get_all('email'):
                info = addresses.get(email_vcl.value) or {}
                info.update(AddressInfo(email_vcl.value, fn.value,
                                        vcard=vcard))
                addresses[email_vcl.value] = info
                for term in terms:
                    info['rank'] += self._boost_rank(term, fn.value,
                                                     email_vcl.value)

        return addresses.values()

    def _index_addresses(self, cfg, terms, vcard_addresses):
        existing = dict([(k['address'].lower(), k) for k in vcard_addresses])
        index = self._idx()

        # Figure out which tags are invisible so we can skip messages marked
        # with those tags.
        invisible = set([t._key for t in cfg.get_tags(flag_hides=True)])

        # 1st, go through the last 1000 or so messages in the index and search
        # for matching senders or recipients, give medium priority.
        matches = {}
        addresses = []
        for msg_idx in xrange(max(0, len(index.INDEX)-2500), len(index.INDEX)):
            msg_info = index.get_msg_at_idx_pos(msg_idx)
            tags = set(msg_info[index.MSG_TAGS].split(','))
            frm = msg_info[index.MSG_FROM]
            match = not (tags & invisible)
            if match:
                for term in terms:
                    if term not in frm.lower():
                        match = False
            if match:
                matches[frm] = matches.get(frm, 0) + 1
            if len(matches) > 1000:
                break

        # FIXME: 2nd, search the social graph for matches, give low priority.
        for frm in index.EMAILS:
            match = True
            for term in terms:
                if term not in frm.lower():
                    match = False
            if match:
                matches[frm] = matches.get(frm, 0) + 1

        # Assign info & scores!
        for frm in matches:
            email, fn = ExtractEmailAndName(frm)

            boost = min(10, matches[frm])
            for term in terms:
                boost += self._boost_rank(term, fn, email)

            if not email or '@' not in email:
                # FIXME: This may not be the right thing for alternate
                #        message transports.
                pass
            elif email.lower() in existing:
                existing[email.lower()]['rank'] += min(20, boost)
            else:
                info = AddressInfo(email, fn)
                existing[email.lower()] = info
                addresses.append(info)

        return addresses

    def command(self):
        session, config = self.session, self.session.config
        if 'q' in self.data:
            terms = [t.lower() for t in self.data['q']]
        else:
            terms = [t.lower() for t in self.args]
        count = int(self.data.get('count', 10))
        offset = int(self.data.get('offset', 0))

        vcard_addrs = self._vcard_addresses(config, terms)
        index_addrs = self._index_addresses(config, terms, vcard_addrs)
        addresses = vcard_addrs + index_addrs
        addresses.sort(key=lambda k: -k['rank'])
        total = len(addresses)
        return {
            'addresses': addresses[offset:min(offset+count, total)],
            'displayed': min(count, total),
            'total': total,
            'offset': offset,
            'count': count,
            'start': offset,
            'end': offset+count,
        }


def ProfileVCard(parent):
    """A factory for generating profile commands"""
    synopsis = [(t and t.replace('vcard', 'profile') or t)
                for t in parent.SYNOPSIS]
    synopsis[2] = synopsis[1]

    class ProfileVCardCommand(parent):
        SYNOPSIS = tuple(synopsis)
        KIND = 'profile'
        ORDER = ('Tagging', 3)
        VCARD = "profile"

    return ProfileVCardCommand


class Profile(ProfileVCard(VCard)):
    """View profile"""


class AddProfile(ProfileVCard(AddVCard)):
    """Add profiles"""


class RemoveProfile(ProfileVCard(RemoveVCard)):
    """Remove a profile"""


class ListProfiles(ProfileVCard(ListVCards)):
    SYNOPSIS = (None, 'profiles', 'profiles', '[--lines] [<terms>]')
    """Find profiles"""


_plugins.register_commands(VCard, AddVCard, RemoveVCard, ListVCards,
                           VCardAddLines, VCardRemoveLines)
_plugins.register_commands(Contact, AddContact, RemoveContact, ListContacts,
                           AddressSearch)
_plugins.register_commands(Profile, AddProfile, RemoveProfile, ListProfiles)
_plugins.register_commands(ContactImport, ContactImporters)
