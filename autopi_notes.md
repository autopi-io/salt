
# Make new release branch

Steps to make new release branch from upstream salt, with local modifications merged.

1. Checkout current local branch `git checkout --track -b v3007.13.x origin/v3007.13.x`
2. Add upstream salt repository `git remote add upstream git@github.com:saltstack/salt.git`
3. Update upstream remote `git fetch --prune --all`
4. Create new version branch `git checkout --no-track -b v3007.14.x 8de1d11a80ddb1301d40f822a98c9d66a869891e` SHA is `Release v3007.14` commit.
5. (Make sure you are on the new branch, but checkout should already have done this.)
5. Merge changes from old branch into the new one we just created. `git merge v3007.13.x`
6. You should now have a new branch with the newest code for that release, from the upstream salt repo, with your own local changes merged.


# Fix PyYAML issue

sudo apt install -y libyaml-dev  # I had already run this earlier...
sudo pip3 uninstall -y pyyaml
sudo pip3 install --no-cache-dir --no-binary pyyaml pyyaml