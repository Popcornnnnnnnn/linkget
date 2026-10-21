"""Generated files and fake native tools exercise Live Photo evidence boundaries."""
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from linkget import live, ownership, transfers


def png(rgb):
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00' + bytes(rgb))) + chunk(b'IEND', b''))


class LiveOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root/'sources'
        self.source.mkdir()
        self.image = self.source/'fixture.png'
        self.movie = self.source/'fixture.mov'
        self.image.write_bytes(png((255, 0, 0)))
        self.movie.write_bytes(b'generated motion fixture')
        self.pair = self.proven_pair(self.image, self.movie)
        self.output_proofs = []
        def record(proofs):
            self.output_proofs.extend(proofs)
        def verify():
            for proof in self.output_proofs:
                proof.verify()
        patcher = patch.object(transfers, 'record_outputs', side_effect=record)
        self.record = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(transfers, 'verify_outputs', side_effect=verify)
        patcher.start()
        self.addCleanup(patcher.stop)

    def proven_pair(self, image, movie):
        return ownership.FileList((image, movie), [ownership.FileProof.observe(image), ownership.FileProof.observe(movie)])

    def swap(self, path, data):
        replacement = path.with_name(path.name + '.replacement')
        replacement.write_bytes(data)
        os.replace(replacement, path)

    def test_created_and_duplicate_pairs_both_register_fixed_destination_proofs(self):
        folder = self.root/'saved'
        self.assertEqual(live.save_pairs([self.pair], folder), ([self.image, self.movie], 0))
        self.assertEqual(len(self.output_proofs), 2)
        self.assertTrue(all(proof.created for proof in self.output_proofs))
        self.output_proofs.clear()
        self.assertEqual(live.save_pairs([self.pair], folder), ([], 2))
        self.assertEqual(len(self.output_proofs), 2)
        self.assertFalse(any(proof.created for proof in self.output_proofs))
        self.assertEqual(self.record.call_count, 2)

    def test_pair_collision_uses_shared_suffix_without_adopting_existing_file(self):
        folder = self.root/'saved'
        folder.mkdir()
        (folder/self.movie.name).write_bytes(b'user file')
        self.assertEqual(live.save_pairs([self.pair], folder), ([self.image, self.movie], 0))
        self.assertEqual((folder/'fixture-1.png').read_bytes(), self.image.read_bytes())
        self.assertEqual((folder/'fixture-1.mov').read_bytes(), self.movie.read_bytes())
        self.assertEqual((folder/self.movie.name).read_bytes(), b'user file')
        self.assertTrue(all('-1.' in proof.path.name for proof in self.output_proofs))

    def test_duplicate_replaced_after_hash_is_refused_and_preserved(self):
        folder = self.root/'saved'
        folder.mkdir()
        for path in self.pair:
            (folder/path.name).write_bytes(path.read_bytes())
        target = folder/self.image.name
        jobs = ownership._jobs()
        actual_digest = jobs.digest
        replaced = []
        def digest(path, *args, **kwargs):
            checksum = actual_digest(path, *args, **kwargs)
            if ownership.canonical(path) == ownership.canonical(target) and not replaced:
                self.swap(target, png((0, 0, 255)))
                replaced.append(target)
            return checksum
        with patch.object(jobs, 'digest', side_effect=digest):
            with self.assertRaises(RuntimeError):
                live.save_pairs([self.pair], folder)
        self.assertEqual(target.read_bytes(), png((0, 0, 255)))
        self.assertEqual(self.image.read_bytes(), png((255, 0, 0)))
        self.record.assert_not_called()

    def test_duplicate_replaced_after_registration_fails_final_output_check(self):
        folder = self.root/'saved'
        folder.mkdir()
        for path in self.pair:
            (folder/path.name).write_bytes(path.read_bytes())
        target = folder/self.image.name
        def record(proofs):
            self.output_proofs.extend(proofs)
            self.swap(target, png((0, 0, 255)))
        self.record.side_effect = record
        with self.assertRaises(RuntimeError):
            live.save_pairs([self.pair], folder)
        self.assertEqual(target.read_bytes(), png((0, 0, 255)))
        self.assertTrue(self.image.exists())

    def test_created_target_replaced_during_copy_is_never_deleted(self):
        folder = self.root/'saved'
        target = folder/self.image.name
        actual_copy = live.shutil.copyfileobj
        def copy(source, output):
            actual_copy(source, output)
            self.swap(Path(output.name), png((0, 0, 255)))
        with patch.object(live.shutil, 'copyfileobj', side_effect=copy):
            with self.assertRaises(RuntimeError):
                live.save_pairs([self.pair], folder)
        self.assertEqual(target.read_bytes(), png((0, 0, 255)))
        self.assertTrue(self.image.exists())
        self.record.assert_not_called()

    def test_second_component_failure_removes_only_unchanged_current_pair_outputs(self):
        folder = self.root/'saved'
        actual_copy = live.shutil.copyfileobj
        count = 0
        def copy(source, output):
            nonlocal count
            count += 1
            if count == 2:
                output.write(b'partial')
                raise OSError('disk full')
            actual_copy(source, output)
        with patch.object(live.shutil, 'copyfileobj', side_effect=copy):
            with self.assertRaises(OSError):
                live.save_pairs([self.pair], folder)
        self.assertEqual(list(folder.iterdir()), [])
        self.assertTrue(all(path.exists() for path in self.pair))

    def test_rollback_keeps_replacement_of_first_component(self):
        folder = self.root/'saved'
        target = folder/self.image.name
        actual_copy = live.shutil.copyfileobj
        count = 0
        def copy(source, output):
            nonlocal count
            count += 1
            if count == 2:
                self.swap(target, png((0, 0, 255)))
                output.write(b'partial')
                raise OSError('disk full')
            actual_copy(source, output)
        with patch.object(live.shutil, 'copyfileobj', side_effect=copy):
            with self.assertRaises(OSError):
                live.save_pairs([self.pair], folder)
        self.assertEqual(target.read_bytes(), png((0, 0, 255)))
        self.assertTrue(all(path.exists() for path in self.pair))

    def test_later_pair_failure_keeps_already_complete_pair(self):
        second_image, second_movie = self.source/'second.png', self.source/'second.mov'
        second_image.write_bytes(png((0, 255, 0)))
        second_movie.write_bytes(b'second generated motion')
        pair = self.proven_pair(second_image, second_movie)
        actual_copy = live.shutil.copyfileobj
        count = 0
        def copy(source, output):
            nonlocal count
            count += 1
            if count == 3:
                output.write(b'partial')
                raise OSError('disk full')
            actual_copy(source, output)
        folder = self.root/'saved'
        with patch.object(live.shutil, 'copyfileobj', side_effect=copy):
            with self.assertRaises(OSError):
                live.save_pairs([self.pair, pair], folder)
        self.assertEqual((folder/self.image.name).read_bytes(), self.image.read_bytes())
        self.assertEqual((folder/self.movie.name).read_bytes(), self.movie.read_bytes())
        self.assertFalse((folder/second_image.name).exists())

    def test_retained_copies_carry_creation_proof_but_existing_copies_are_observed(self):
        folder = self.root/'retained'
        first = live.retain_pairs([self.pair], folder)
        self.assertIsInstance(first, ownership.FileList)
        self.assertTrue(all(first.proof(path).created for path in first))
        second = live.retain_pairs([self.pair], folder)
        self.assertFalse(any(second.proof(path).created for path in second))
        for path in second:
            with self.assertRaises(ValueError):
                second.proof(path).remove()
            self.assertTrue(path.exists())

    def test_retained_proof_never_reobserves_a_later_replacement(self):
        files = live.retain_pairs([self.pair], self.root/'retained')
        target = files[0]
        self.swap(target, png((0, 0, 255)))
        with self.assertRaises(RuntimeError):
            files.proof(target)
        self.assertEqual(target.read_bytes(), png((0, 0, 255)))

    def test_source_filelist_rejects_valid_replacement_before_copy(self):
        self.swap(self.image, png((0, 0, 255)))
        folder = self.root/'saved'
        with self.assertRaises(RuntimeError):
            live.save_pairs([self.pair], folder)
        self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(self.image.read_bytes(), png((0, 0, 255)))

    def fake_tools(self, command):
        if len(command) > 1 and command[1] == 'template':
            Path(command[3]).write_bytes(b'generated metadata template')
        elif len(command) > 1 and command[1] == 'movie':
            Path(command[3]).write_bytes(b'generated paired motion:' + Path(command[2]).read_bytes())
        elif command[0] == 'fake-exiftool':
            path = Path(command[-1])
            path.write_bytes(path.read_bytes() + b'generated pairing metadata')

    def prepare(self, run=None, pairs=None):
        with patch.object(live.sys, 'platform', 'darwin'), patch.object(live.shutil, 'which', return_value='fake-exiftool'), \
                patch.object(live, 'run', side_effect=run or self.fake_tools):
            return live.prepare_pairs(self.pair, pairs or [(self.image.name, self.movie.name)], self.root/'prepared')

    def test_prepared_resources_carry_nonowning_proof_and_repeat_does_not_reobserve(self):
        pairs = self.prepare(pairs=[(self.image.name, self.movie.name)] * 2)
        self.assertEqual(len(pairs), 2)
        self.assertIs(pairs[0], pairs[1])
        self.assertIsInstance(pairs[0], ownership.FileList)
        expected = hashlib.sha256((self.pair.proof(self.image).sha256 + self.pair.proof(self.movie).sha256).encode()).hexdigest()
        self.assertIn(expected, pairs[0][0].name)
        self.assertFalse(any(pairs[0].proof(path).created for path in pairs[0]))
        for path in pairs[0]:
            with self.assertRaises(ValueError):
                pairs[0].proof(path).remove()
            self.assertTrue(path.exists())

    def test_preparation_refuses_source_replacement_during_fake_tools(self):
        def run(command):
            self.fake_tools(command)
            if len(command) > 1 and command[1] == 'movie':
                self.swap(self.image, png((0, 0, 255)))
        with self.assertRaises(RuntimeError):
            self.prepare(run)
        self.assertEqual(self.image.read_bytes(), png((0, 0, 255)))

    def test_prepared_output_replaced_after_observation_is_refused(self):
        replaced = []
        def run(command):
            self.fake_tools(command)
            if len(command) > 1 and command[1] == 'validate':
                target = Path(command[2])
                self.swap(target, png((0, 0, 255)))
                replaced.append(target)
        with self.assertRaises(RuntimeError):
            self.prepare(run)
        self.assertEqual(replaced[0].read_bytes(), png((0, 0, 255)))


if __name__ == '__main__':
    unittest.main()
