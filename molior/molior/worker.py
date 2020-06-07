import shutil
import asyncio

from ..app import logger
from ..ops import GitClone, get_latest_tag
from ..ops import BuildProcess, ScheduleBuilds, CreateBuildEnv
from ..tools import write_log, write_log_title

from ..model.database import Session
from ..model.build import Build
from ..model.chroot import Chroot
from ..model.sourcerepository import SourceRepository


class Worker:
    """
    Source Packaging worker task

    """

    def __init__(self, task_queue, aptly_queue):
        self.task_queue = task_queue
        self.aptly_queue = aptly_queue

    async def _clone(self, args, session):
        logger.debug("worker: got clone task")
        build_id = args[0]
        repo_id = args[1]

        build = session.query(Build).filter(Build.id == build_id).first()
        if not build:
            logger.error("clone: build %d not found", build_id)
            return

        repo = (
            session.query(SourceRepository)
            .filter(SourceRepository.id == repo_id)
            .first()
        )
        if not repo:
            logger.error("buildlatest: repo %d not found", repo_id)
            return

        if repo.state != "new" and repo.state != "error":
            logger.error(
                "Repository with id '%d' not ready for clone, ignoring request", repo_id
            )
            return

        repo.set_cloning()
        session.commit()

        asyncio.ensure_future(GitClone(build_id, repo.id, self.task_queue))

    async def _build(self, args, session):
        logger.debug("worker: got build task")

        build_id = args[0]
        repo_id = args[1]
        git_ref = args[2]
        ci_branch = args[3]

        build = session.query(Build).filter(Build.id == build_id).first()
        if not build:
            logger.error("build: build %d not found", build_id)
            return

        if build.buildstate != "building":
            await build.set_building()

        repo = session.query(SourceRepository).filter(SourceRepository.id == repo_id).first()
        if not repo:
            logger.error("build: repo %d not found", repo_id)
            return
        if repo.state != "ready":
            logger.info("worker: repo %d not ready, requeueing", repo_id)
            await self.task_queue.put({"build": args})
            await asyncio.sleep(2)
            return

        repo.set_busy()
        session.commit()

        asyncio.ensure_future(BuildProcess(self.task_queue, self.aptly_queue, build_id, repo.id, git_ref, ci_branch))

    async def _buildlatest(self, args, session):
        logger.debug("worker: got buildlatest task")
        repo_id = args[0]
        build_id = args[1]

        build = session.query(Build).filter(Build.id == build_id).first()
        if not build:
            logger.error("buildlatest: build %d not found", build_id)
            return

        repo = session.query(SourceRepository).filter(SourceRepository.id == repo_id).first()
        if not repo:
            logger.error("buildlatest: repo %d not found", repo_id)
            return
        if repo.state != "ready":
            await self.task_queue.put({"buildlatest": args})
            logger.info("worker: repo %d not ready, requeueing", repo_id)
            await asyncio.sleep(2)
            return

        if build.buildstate != "building":
            await build.set_building()

        repo.set_busy()
        session.commit()  # pylint: disable=no-member

        await write_log_title(build.id, "Checking Repository")

        await write_log(build.id, "I: fetching git tags\n")
        try:
            # this does a git fetch
            latest_tag = await get_latest_tag(repo.src_path, build_id)
        except Exception as exc:
            logger.error("worker: error getting latest git tag")
            await write_log(build.id, "E: Error getting git tags\n")
            await write_log_title(build.id, "Done", no_footer_newline=True, no_header_newline=False)
            logger.exception(exc)
            await build.set_failed()
            repo.set_ready()
            session.commit()  # pylint: disable=no-member
            return

        repo.set_ready()
        session.commit()  # pylint: disable=no-member

        if not latest_tag:
            logger.error("sourcerepository '%s' has no release tag", repo.url)
            await write_log(build.id, "E: no git tags found\n")
            await write_log_title(build.id, "Done", no_footer_newline=True, no_header_newline=False)
            await build.set_failed()
            session.commit()  # pylint: disable=no-member
            return

        await write_log(build.id, "\n")
        git_ref = str(latest_tag)
        args = {"build": [build_id, repo_id, git_ref, None]}
        await self.task_queue.put(args)

    async def _rebuild(self, args, session):
        logger.debug("worker: got rebuild task")
        build_id = args[0]
        build = session.query(Build).filter(Build.id == build_id).first()
        if not build:
            logger.error("rebuild: build %d not found", build_id)
            return

        ok = False
        if build.buildtype == "deb":
            if build.buildstate == "build_failed":
                buildout = "/var/lib/molior/buildout/%d" % build_id
                logger.info("removing %s", buildout)
                try:
                    shutil.rmtree(buildout)
                except Exception as exc:
                    logger.exception(exc)
                    pass

                await build.set_needs_build()
                session.commit()

                args = {"schedule": []}
                await self.task_queue.put(args)
                ok = True

        if build.buildtype == "source":
            if build.buildstate == "publish_failed":
                await build.set_needs_publish()
                session.commit()
                await write_log(build.parent.id, "I: publishing source package\n")
                await self.aptly_queue.put({"src_publish": [build.id]})

        if build.buildtype == "chroot":
            if build.buildstate == "build_failed":
                chroot = session.query(Chroot).filter(Chroot.build_id == build_id).first()
                if not chroot:
                    logger.error("rebuild: chroot not found")
                else:
                    args = {"buildenv": [
                            chroot.id,
                            build_id,
                            chroot.buildvariant.base_mirror.mirror_distribution,
                            chroot.buildvariant.base_mirror.project.name,
                            chroot.buildvariant.base_mirror.name,
                            chroot.buildvariant.architecture.name,
                            chroot.buildvariant.base_mirror.mirror_components
                            ]}
                    logger.info("queueing {}".format(args))
                    await self.task_queue.put(args)
                    ok = True

        if not ok:
            logger.error("rebuilding {} build in state {} not supported".format(build.buildtype, build.buildstate))

    async def _schedule(self, _):
        asyncio.ensure_future(ScheduleBuilds())

    async def _buildenv(self, args):
        chroot_id = args[0]
        build_id = args[1]
        dist = args[2]
        name = args[3]
        version = args[4]
        arch = args[5]
        components = args[6]
        logger.info("asyncio.ensure_future(CreateBuildEnv")
        asyncio.ensure_future(CreateBuildEnv(self.task_queue, chroot_id, build_id, dist, name, version, arch, components))

    async def run(self):
        """
        Run the worker task.
        """

        while True:
            session = None
            try:
                task = await self.task_queue.get()
                if task is None:
                    logger.info("worker: got emtpy task, aborting...")
                    break

                logger.info("worker: got task {}".format(task))
                with Session() as session:

                    handled = False
                    args = task.get("clone")
                    if args:
                        handled = True
                        await self._clone(args, session)

                    if not handled:
                        args = task.get("build")
                        if args:
                            handled = True
                            await self._build(args, session)

                    if not handled:
                        args = task.get("buildlatest")
                        if args:
                            handled = True
                            await self._buildlatest(args, session)

                    if not handled:
                        args = task.get("rebuild")
                        if args:
                            handled = True
                            await self._rebuild(args, session)

                    if not handled:
                        args = task.get("schedule")
                        if args:
                            handled = True
                            await self._schedule(session)

                    if not handled:
                        args = task.get("buildenv")
                        if args:
                            handled = True
                            await self._buildenv(args)

                    if not handled:
                        logger.error("worker got unknown task %s", str(task))

                    self.task_queue.task_done()

            except Exception as exc:
                logger.exception(exc)

        logger.info("terminating worker task")
